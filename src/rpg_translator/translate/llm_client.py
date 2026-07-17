from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

# 429（限流）和 5xx 视为瞬时故障，值得退避重试；401/403/400 这类重试了也没用，
# 直接换下一个 provider，不在当前 provider 上浪费重试次数和等待时间。
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


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
    """

    def __init__(
        self,
        configs: LLMConfig | list[LLMConfig],
        max_retries_per_provider: int = 3,
        backoff_base_seconds: float = 1.0,
        transports: list[httpx.BaseTransport | None] | None = None,
    ):
        config_list = [configs] if isinstance(configs, LLMConfig) else list(configs)
        if not config_list:
            raise ValueError("至少需要一个 LLMConfig")
        transports = transports or [None] * len(config_list)

        self._configs = config_list
        self._max_retries = max_retries_per_provider
        self._backoff_base_seconds = backoff_base_seconds
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

        for config, http in zip(self._configs, self._http_clients):
            for attempt in range(self._max_retries):
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
                        },
                    )
                    response.raise_for_status()
                    return response.json()["choices"][0]["message"]["content"]
                except httpx.HTTPStatusError as e:
                    last_error = e
                    if e.response.status_code not in _RETRYABLE_STATUS_CODES:
                        break  # 换下一个 provider，不在这个 provider 上继续重试
                except httpx.TransportError as e:
                    last_error = e

                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._backoff_base_seconds * (2**attempt))

        assert last_error is not None
        raise last_error
