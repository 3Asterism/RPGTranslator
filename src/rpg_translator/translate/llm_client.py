from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

import httpx

# 429（限流）和 5xx 视为瞬时故障，值得退避重试；401/403/400 这类重试了也没用，
# 直接换下一个 provider，不在当前 provider 上浪费重试次数和等待时间。
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# 429 专用的冷却时间（区别于 5xx/连接失败用的小步指数退避 _backoff_base_seconds）：
# provider 没给 Retry-After 时的默认冷却、连续被限流时的封顶冷却。真实的限流窗口
# 通常以“秒/分钟”为单位重置，用 1/2/4 秒这种小退避去试探只会一直撞在同一个窗口上，
# 换来更长的冷却时间才有意义。
_DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 10.0
_MAX_RATE_LIMIT_COOLDOWN_SECONDS = 60.0


def _parse_retry_after(value: str | None) -> float | None:
    """只解析 delay-seconds 形式的 Retry-After（OpenAI 兼容网关的通行做法）；
    HTTP-date 形式或缺失都返回 None，交给调用方用默认冷却时间兜底。"""
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"


class LLMClient:
    """OpenAI 兼容协议的聊天补全客户端（面向 DeepSeek，也兼容其他同协议服务商）。

    system prompt（术语表 + 固定指令）放前面、user prompt（本批次待译文本）放后面，
    方便复用同一份 system prompt 时吃到 DeepSeek 的 context caching 折扣。

    支持传入多个 LLMConfig 做故障转移：某个 provider 连续报瞬时错误（429/5xx/连接
    失败）时按指数退避重试，重试次数用尽后换下一个 provider 循环使用；非瞬时错误
    （比如 401 key 不对）不在当前 provider 上浪费重试，直接换下一个。

    429 限流会在每个 provider 上维护一个共享的冷却截止时间：同一个 LLMClient 实例
    被多个并发任务（见 batch_translator.py 的 semaphore）共用，一旦有任意一个请求
    撞到 429，后续所有对同一个 provider 的调用（不管是当前调用的下一次重试，还是
    别的并发任务发起的全新调用）在真正发请求前都会先等这个冷却过去，而不是各自按
    自己的小步退避独立重试——高并发下"各自重试"等于一直在同一个限流窗口里反复
    冲撞，"共享冷却"才是把并发请求真正聚合、错峰的关键。
    """

    def __init__(
        self,
        configs: LLMConfig | list[LLMConfig],
        max_retries_per_provider: int = 3,
        backoff_base_seconds: float = 1.0,
        transports: list[httpx.BaseTransport | None] | None = None,
        on_usage: Callable[[str, int, int], None] | None = None,
    ):
        config_list = [configs] if isinstance(configs, LLMConfig) else list(configs)
        if not config_list:
            raise ValueError("至少需要一个 LLMConfig")
        transports = transports or [None] * len(config_list)

        self._configs = config_list
        self._max_retries = max_retries_per_provider
        self._backoff_base_seconds = backoff_base_seconds
        # 每个 provider 一个冷却截止时间（time.monotonic() 基准）+ 连续限流命中计数
        # （命中一次翻倍冷却时长，封顶见 _MAX_RATE_LIMIT_COOLDOWN_SECONDS；任意一次
        # 成功清零，避免冷却时间只涨不跌）。
        self._rate_limited_until = [0.0] * len(config_list)
        self._consecutive_rate_limit_hits = [0] * len(config_list)
        # on_usage(model, prompt_tokens, completion_tokens) 每次调用成功后回调一次，供
        # GUI 实时统计本次会话的 token 用量/预估花费（见 gui/main_window.py 状态栏）；
        # 不传就跳过，不影响任何现有调用方。
        self._on_usage = on_usage
        self._http_clients = [
            httpx.AsyncClient(
                base_url=_normalize_base_url(c.base_url),
                headers={"Authorization": f"Bearer {c.api_key}"},
                timeout=c.timeout,
                transport=t,
            )
            for c, t in zip(config_list, transports)
        ]

    async def aclose(self) -> None:
        for http in self._http_clients:
            await http.aclose()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        last_error: Exception | None = None

        for provider_idx, (config, http) in enumerate(zip(self._configs, self._http_clients)):
            for attempt in range(self._max_retries):
                cooldown_remaining = self._rate_limited_until[provider_idx] - time.monotonic()
                if cooldown_remaining > 0:
                    await asyncio.sleep(cooldown_remaining)
                try:
                    response = await http.post(
                        "chat/completions",
                        json={
                            "model": config.model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                            "stream": False,
                            # DeepSeek-V4-Flash / Qwen3.6 这类混合思考模型默认开思考模式，
                            # 会多算一大段 reasoning_content 且计入 completion tokens——
                            # 实测同一句翻译请求，开思考模式 completion_tokens 200+，关掉后
                            # 只要 1，纯属为翻译这种机械任务多付的钱。这里强制关闭；对不认识
                            # 这个字段的 provider，未知参数按 OpenAI 兼容协议惯例会被忽略，
                            # 不影响调用。
                            "enable_thinking": False,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    self._consecutive_rate_limit_hits[provider_idx] = 0
                    if self._on_usage is not None:
                        usage = data.get("usage") or {}
                        self._on_usage(
                            config.model,
                            usage.get("prompt_tokens", 0),
                            usage.get("completion_tokens", 0),
                        )
                    return data["choices"][0]["message"]["content"]
                except httpx.HTTPStatusError as e:
                    last_error = e
                    if e.response.status_code == 429:
                        hits = self._consecutive_rate_limit_hits[provider_idx]
                        retry_after = _parse_retry_after(e.response.headers.get("retry-after"))
                        cooldown = retry_after if retry_after is not None else min(
                            _DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS * (2**hits),
                            _MAX_RATE_LIMIT_COOLDOWN_SECONDS,
                        )
                        self._consecutive_rate_limit_hits[provider_idx] = hits + 1
                        self._rate_limited_until[provider_idx] = time.monotonic() + cooldown
                        continue  # 冷却在下一次循环开头统一等，不再叠加下面的固定退避
                    if e.response.status_code not in _RETRYABLE_STATUS_CODES:
                        break  # 换下一个 provider，不在这个 provider 上继续重试
                except httpx.TransportError as e:
                    last_error = e

                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._backoff_base_seconds * (2**attempt))

        assert last_error is not None
        raise last_error
