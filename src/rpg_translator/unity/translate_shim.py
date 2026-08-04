from __future__ import annotations

import asyncio
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

from rpg_translator.translate.llm_client import LLMClient, LLMConfig
from rpg_translator.unity.placeholders import protect, restore
from rpg_translator.unity.prompt import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

_HOST = "127.0.0.1"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_HOST, 0))
        return sock.getsockname()[1]


async def translate_text(
    config: LLMConfig,
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """单条无状态翻译：保护占位符 -> 拼 prompt -> 调 LLMClient -> 还原占位符。
    每次请求起一个短生命周期的 LLMClient（而不是复用一个跨请求的单例）——
    XUnity 请求是交互式、低频的（用户读完一句文本才会触发下一句渲染），起一个
    新 httpx.AsyncClient 的开销可以忽略，换来的是不用操心跨线程/跨事件循环
    共享同一个 client 的生命周期问题（见 TranslateShimServer 里每个请求单独
    asyncio.run() 的说明）。"""
    protected, tokens = protect(text)
    user_prompt = build_user_prompt(protected, source_lang, target_lang)
    async with LLMClient(config, transports=[transport] if transport is not None else None) as client:
        translated_protected = await client.chat(SYSTEM_PROMPT, user_prompt)
    return restore(translated_protected.strip(), tokens)


def _make_handler(
    config: LLMConfig, transport: httpx.BaseTransport | None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            logger.debug("shim: " + format, *args)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
            parsed = urlparse(self.path)
            if parsed.path != "/translate":
                self.send_response(404)
                self.end_headers()
                return

            params = parse_qs(parsed.query)
            text = params.get("text", [""])[0]
            if not text:
                self.send_response(400)
                self.end_headers()
                return
            source_lang = params.get("from", ["ja"])[0]
            target_lang = params.get("to", ["zh-CN"])[0]

            try:
                translated = asyncio.run(
                    translate_text(config, text, source_lang, target_lang, transport=transport)
                )
            except Exception:
                logger.exception("shim 翻译请求失败：%r", text)
                self.send_response(502)
                self.end_headers()
                return

            body = translated.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


class TranslateShimServer:
    """本机常驻 HTTP server，实现 XUnity.AutoTranslator 的 CustomTranslate 端点
    契约（GET /translate?from=&to=&text= -> 纯文本译文）。跟
    translate/local_engine.py 的 LocalEngineProcess 同一种"跟随 App 生命周期
    手动 start/stop"的用法，不是 QThread——GUI 层持有一个实例，deploy 前/中
    start()，closeEvent 里 stop()。"""

    def __init__(self, config: LLMConfig, *, transport: httpx.BaseTransport | None = None):
        self._config = config
        self._transport = transport
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None

    @property
    def port(self) -> int | None:
        return self._port

    def is_running(self) -> bool:
        return self._server is not None

    def start(self) -> int:
        if self._server is not None:
            assert self._port is not None
            return self._port

        port = _find_free_port()
        handler_cls = _make_handler(self._config, self._transport)
        server = ThreadingHTTPServer((_HOST, port), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        self._server = server
        self._thread = thread
        self._port = port
        return port

    def stop(self) -> None:
        if self._server is None:
            return
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._port = None
