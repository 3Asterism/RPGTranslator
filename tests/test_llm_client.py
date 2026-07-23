from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.translate import llm_client as llm_client_module
from rpg_translator.translate.llm_client import LLMClient, LLMConfig, _normalize_base_url


def test_normalize_base_url_always_ends_with_slash():
    assert _normalize_base_url("https://api.deepseek.com") == "https://api.deepseek.com/"
    assert _normalize_base_url("https://api.siliconflow.cn/v1") == "https://api.siliconflow.cn/v1/"
    assert _normalize_base_url("https://api.deepseek.com/") == "https://api.deepseek.com/"


def test_relative_join_preserves_base_path_suffix_like_v1():
    # httpx/URL 的 join 是按 WHATWG URL 规则做的：如果拼接的相对路径带前导 "/"，
    # 会把 base_url 里的 path（比如 /v1）整个替换掉。这里验证 _normalize_base_url +
    # 不带前导斜杠的相对路径，两种 base_url 写法（带 /v1 后缀 / 不带）都能正确拼接。
    with_suffix = httpx.URL(_normalize_base_url("https://api.siliconflow.cn/v1"))
    assert str(with_suffix.join("chat/completions")) == "https://api.siliconflow.cn/v1/chat/completions"

    without_suffix = httpx.URL(_normalize_base_url("https://api.deepseek.com"))
    assert str(without_suffix.join("chat/completions")) == "https://api.deepseek.com/chat/completions"


@pytest.mark.anyio
async def test_real_chat_completion_against_configured_provider():
    settings = Settings()
    api_key = get_deepseek_api_key()
    if not api_key:
        pytest.skip("本地未配置 DEEPSEEK_API_KEY（.env 不存在或未设置），跳过真实 API 调用测试")

    config = LLMConfig(
        api_key=api_key, base_url=settings.deepseek_base_url, model=settings.deepseek_model
    )
    async with LLMClient(config) as client:
        result = await client.chat(
            "你是一个只输出单个词的翻译助手，不要输出任何解释或标点。",
            "把「こんにちは」翻译成简体中文。",
        )

    assert result.strip()
    assert "你好" in result


def _success_response(content: str, usage: dict | None = None) -> httpx.Response:
    body = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def _error_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, json={"error": "boom"})


@pytest.mark.anyio
async def test_chat_retries_transient_503_then_succeeds():
    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _error_response(503) if call_count < 3 else _success_response("成功了")

    config = LLMConfig(api_key="x", base_url="https://a.test", model="m")
    client = LLMClient(config, backoff_base_seconds=0.01, transports=[httpx.MockTransport(handler)])
    try:
        result = await client.chat("sys", "user")
    finally:
        await client.aclose()

    assert result == "成功了"
    assert call_count == 3


@pytest.mark.anyio
async def test_chat_falls_back_to_next_provider_after_exhausting_retries():
    provider_a_calls = 0

    async def handler_a(request: httpx.Request) -> httpx.Response:
        nonlocal provider_a_calls
        provider_a_calls += 1
        return _error_response(503)

    async def handler_b(request: httpx.Request) -> httpx.Response:
        return _success_response("provider B 成功")

    configs = [
        LLMConfig(api_key="a", base_url="https://a.test", model="m"),
        LLMConfig(api_key="b", base_url="https://b.test", model="m"),
    ]
    client = LLMClient(
        configs,
        max_retries_per_provider=2,
        backoff_base_seconds=0.01,
        transports=[httpx.MockTransport(handler_a), httpx.MockTransport(handler_b)],
    )
    try:
        result = await client.chat("sys", "user")
    finally:
        await client.aclose()

    assert result == "provider B 成功"
    assert provider_a_calls == 2  # 用满了 provider A 的重试预算才换下一个


@pytest.mark.anyio
async def test_chat_skips_retry_on_non_retryable_status_and_moves_to_next_provider():
    provider_a_calls = 0

    async def handler_a(request: httpx.Request) -> httpx.Response:
        nonlocal provider_a_calls
        provider_a_calls += 1
        return _error_response(401)

    async def handler_b(request: httpx.Request) -> httpx.Response:
        return _success_response("provider B 成功")

    configs = [
        LLMConfig(api_key="bad", base_url="https://a.test", model="m"),
        LLMConfig(api_key="good", base_url="https://b.test", model="m"),
    ]
    client = LLMClient(
        configs,
        max_retries_per_provider=3,
        backoff_base_seconds=0.01,
        transports=[httpx.MockTransport(handler_a), httpx.MockTransport(handler_b)],
    )
    try:
        result = await client.chat("sys", "user")
    finally:
        await client.aclose()

    assert result == "provider B 成功"
    assert provider_a_calls == 1  # 401 不该被当瞬时错误重试 3 次，试一次就该换下一个 provider


@pytest.mark.anyio
async def test_chat_raises_last_error_when_all_providers_exhausted():
    async def always_503(request: httpx.Request) -> httpx.Response:
        return _error_response(503)

    configs = [
        LLMConfig(api_key="a", base_url="https://a.test", model="m"),
        LLMConfig(api_key="b", base_url="https://b.test", model="m"),
    ]
    client = LLMClient(
        configs,
        max_retries_per_provider=2,
        backoff_base_seconds=0.01,
        transports=[httpx.MockTransport(always_503), httpx.MockTransport(always_503)],
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.chat("sys", "user")
    finally:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_response",
    [
        pytest.param(httpx.Response(200, json={"choices": []}), id="empty_choices"),
        pytest.param(
            httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
            id="null_content",
        ),
        pytest.param(httpx.Response(200, content=b"not json at all"), id="invalid_json_body"),
    ],
)
async def test_chat_retries_then_fails_over_on_malformed_2xx_response(bad_response):
    """响应状态码是 2xx 但内容对不上预期形状——choices 为空（网关内容审核拒绝却
    没有回退成 4xx）、content 为 None（混合思考模型只填了 reasoning_content）、
    body 干脆不是合法 JSON（网关截断响应/返回错误页面却还是 200）。这三种情况
    httpx 都不会抛异常（状态码本身是 2xx），之前要么直接访问 data["choices"][0]
    抛 KeyError/IndexError、要么 response.json() 抛 json.JSONDecodeError——这些
    都不是 httpx.HTTPStatusError/TransportError 的子类，会直接逃出 chat()、跳过
    还没试过的 provider B（对 null_content 更糟：不抛异常，把 None 当成合法译文
    静默返回，污染下游翻译结果）。修复后三种都应该走跟 5xx 一样的重试/换 provider
    路径，最终从 provider B 拿到结果。"""

    async def handler_a(request: httpx.Request) -> httpx.Response:
        return bad_response

    async def handler_b(request: httpx.Request) -> httpx.Response:
        return _success_response("provider B 成功")

    configs = [
        LLMConfig(api_key="a", base_url="https://a.test", model="m"),
        LLMConfig(api_key="b", base_url="https://b.test", model="m"),
    ]
    client = LLMClient(
        configs,
        max_retries_per_provider=1,
        backoff_base_seconds=0.01,
        transports=[httpx.MockTransport(handler_a), httpx.MockTransport(handler_b)],
    )
    try:
        result = await client.chat("sys", "user")
    finally:
        await client.aclose()

    assert result == "provider B 成功"


@pytest.mark.anyio
async def test_chat_reports_usage_via_callback():
    """响应里的 usage 字段（prompt_tokens/completion_tokens）要能通过 on_usage 回调
    传出去，连同这次实际生效的 model 名——GUI 靠这个统计 token 用量/预估花费。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return _success_response("翻译结果", usage={"prompt_tokens": 120, "completion_tokens": 30})

    calls: list[tuple[str, int, int]] = []
    config = LLMConfig(api_key="x", base_url="https://a.test", model="deepseek-v4-flash")
    client = LLMClient(
        config,
        transports=[httpx.MockTransport(handler)],
        on_usage=lambda model, p, c: calls.append((model, p, c)),
    )
    try:
        await client.chat("sys", "user")
    finally:
        await client.aclose()

    assert calls == [("deepseek-v4-flash", 120, 30)]


@pytest.mark.anyio
async def test_chat_missing_usage_field_does_not_crash_callback():
    """有些兼容实现可能不返回 usage 字段，回调应该拿到 0/0 而不是直接报错。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return _success_response("翻译结果")  # 不带 usage

    calls: list[tuple[str, int, int]] = []
    config = LLMConfig(api_key="x", base_url="https://a.test", model="m")
    client = LLMClient(
        config,
        transports=[httpx.MockTransport(handler)],
        on_usage=lambda model, p, c: calls.append((model, p, c)),
    )
    try:
        await client.chat("sys", "user")
    finally:
        await client.aclose()

    assert calls == [("m", 0, 0)]


@pytest.mark.anyio
async def test_chat_real_failover_from_broken_key_to_working_provider():
    """真实验证跨平台故障转移：第一个 provider 用一个真实但错误的 key（真实触发
    401），第二个 provider 用真实可用的 key，验证最终确实能从第二个拿到结果。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        pytest.skip("本地未配置 DEEPSEEK_API_KEY，跳过真实 API 调用测试")

    settings = Settings()
    configs = [
        LLMConfig(api_key="sk-deliberately-invalid-key-for-failover-test", base_url=settings.deepseek_base_url, model=settings.deepseek_model),
        LLMConfig(api_key=api_key, base_url=settings.deepseek_base_url, model=settings.deepseek_model),
    ]
    async with LLMClient(configs, max_retries_per_provider=1) as client:
        result = await client.chat("你是一个只输出单个词的翻译助手。", "把「こんにちは」翻译成简体中文。")

    assert result.strip()


def _rate_limited_response(retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return httpx.Response(429, headers=headers, json={"error": "rate limited"})


@pytest.mark.anyio
async def test_chat_honors_retry_after_header_instead_of_generic_backoff():
    """429 带 Retry-After 时应该按这个头等待，而不是走 5xx/连接失败那套小步指数退避——
    backoff_base_seconds 故意设得很大，如果冷却机制没生效、退回到用了通用退避，
    这个测试会因为超时/耗时过长而失败。"""
    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _rate_limited_response("0.05") if call_count == 1 else _success_response("成功了")

    config = LLMConfig(api_key="x", base_url="https://a.test", model="m")
    client = LLMClient(
        config, backoff_base_seconds=5.0, transports=[httpx.MockTransport(handler)]
    )
    try:
        start = time.monotonic()
        result = await asyncio.wait_for(client.chat("sys", "user"), timeout=2.0)
        elapsed = time.monotonic() - start
    finally:
        await client.aclose()

    assert result == "成功了"
    assert call_count == 2
    assert elapsed < 1.0  # 远小于 backoff_base_seconds=5.0，证明走的是 Retry-After 冷却


@pytest.mark.anyio
async def test_chat_rate_limit_cooldown_shared_across_concurrent_calls():
    """核心场景：批量翻译时很多并发任务共用同一个 LLMClient。其中一个请求撞到 429
    之后，另一个此刻才发起的全新调用（不是同一次 chat() 内部的重试）也应该先等
    共享冷却过去，不能立刻打进去再撞一次限流——这是"聚合退避"而不是"各自重试"的
    关键验证点。"""
    call_times: list[float] = []
    first_429_seen = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        call_times.append(time.monotonic())
        if len(call_times) == 1:
            first_429_seen.set()
            return _rate_limited_response("0.15")
        return _success_response("成功了")

    config = LLMConfig(api_key="x", base_url="https://a.test", model="m")
    client = LLMClient(
        config, max_retries_per_provider=2, transports=[httpx.MockTransport(handler)]
    )
    try:
        task1 = asyncio.ensure_future(client.chat("sys", "user1"))
        await asyncio.wait_for(first_429_seen.wait(), timeout=1.0)
        # task2 是全新的一次 chat() 调用（模拟另一个并发任务），故意在 task1 还在
        # 冷却等待期间才发起
        task2 = asyncio.ensure_future(client.chat("sys", "user2"))

        result1 = await asyncio.wait_for(task1, timeout=2.0)
        result2 = await asyncio.wait_for(task2, timeout=2.0)
    finally:
        await client.aclose()

    assert result1 == "成功了"
    assert result2 == "成功了"
    # 3 次请求：初始 429 + task1 的重试 + task2 自己的首次尝试——两个"冷却之后"的
    # 调用都不能省，关键约束是时机：谁都不能在冷却截止之前抢先打进去撞一次限流。
    assert len(call_times) == 3
    cooldown_deadline = call_times[0] + 0.15
    assert all(t >= cooldown_deadline - 0.02 for t in call_times[1:])


@pytest.mark.anyio
async def test_chat_uses_default_cooldown_when_retry_after_missing(monkeypatch):
    monkeypatch.setattr(llm_client_module, "_DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS", 0.05)
    monkeypatch.setattr(llm_client_module, "_MAX_RATE_LIMIT_COOLDOWN_SECONDS", 0.2)
    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _rate_limited_response() if call_count == 1 else _success_response("成功了")

    config = LLMConfig(api_key="x", base_url="https://a.test", model="m")
    client = LLMClient(
        config, backoff_base_seconds=5.0, transports=[httpx.MockTransport(handler)]
    )
    try:
        start = time.monotonic()
        result = await asyncio.wait_for(client.chat("sys", "user"), timeout=2.0)
        elapsed = time.monotonic() - start
    finally:
        await client.aclose()

    assert result == "成功了"
    assert elapsed < 1.0
