from __future__ import annotations

import json

import httpx
import pytest

from rpg_translator.translate.llm_client import LLMConfig
from rpg_translator.unity.translate_shim import TranslateShimServer, translate_text


def _mock_transport(*, echo_prefix: str = "译:") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_msg = next(m["content"] for m in body["messages"] if m["role"] == "user")
        # 简单模拟：把 user prompt 里最后一行（协议保护后的原文）原样回显，
        # 前面加个前缀，用来验证占位符 token 真的原样传到了"LLM"侧又传了回来。
        payload_line = user_msg.splitlines()[-1]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": f"{echo_prefix}{payload_line}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    return httpx.MockTransport(handler)


def _config() -> LLMConfig:
    return LLMConfig(api_key="sk-test", base_url="http://mock.invalid/v1", model="test-model")


@pytest.mark.anyio
async def test_translate_text_protects_and_restores_placeholders():
    text = "你好 {player_name}"
    result = await translate_text(_config(), text, "ja", "zh-CN", transport=_mock_transport())

    assert "{player_name}" in result
    assert result.startswith("译:")


def test_shim_server_start_returns_free_port_and_stop_releases_it():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port = server.start()

    assert server.is_running()
    assert isinstance(port, int) and port > 0

    server.stop()
    assert not server.is_running()


def test_shim_server_handles_translate_get_request_with_plain_text_response():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port = server.start()
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{port}/translate",
            params={"from": "ja", "to": "zh-CN", "text": "こんにちは"},
            timeout=5.0,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text.startswith("译:")
    finally:
        server.stop()


def test_shim_server_returns_400_when_text_param_missing():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port = server.start()
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/translate", params={"from": "ja", "to": "zh-CN"}, timeout=5.0)
        assert resp.status_code == 400
    finally:
        server.stop()


def test_shim_server_returns_404_for_unknown_path():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port = server.start()
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/unknown", timeout=5.0)
        assert resp.status_code == 404
    finally:
        server.stop()


def test_shim_server_returns_502_when_llm_call_fails():
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    server = TranslateShimServer(_config(), transport=httpx.MockTransport(failing_handler))
    port = server.start()
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{port}/translate", params={"text": "hi"}, timeout=5.0
        )
        assert resp.status_code == 502
    finally:
        server.stop()


def test_shim_server_start_twice_returns_same_port_without_restarting():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    port1 = server.start()
    port2 = server.start()
    assert port1 == port2
    server.stop()


def test_shim_server_stop_without_start_is_a_noop():
    server = TranslateShimServer(_config(), transport=_mock_transport())
    server.stop()  # 不应该抛异常
    assert not server.is_running()
