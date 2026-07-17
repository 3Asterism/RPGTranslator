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
