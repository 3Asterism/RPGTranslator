from __future__ import annotations

import httpx
import pytest

from rpg_translator.config import Settings, get_deepseek_api_key
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


def _success_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


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
