from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from rpg_translator.core.evb_unpack import unpack_evb
from rpg_translator.core.pipeline import run_extract, run_inject, run_translate
from rpg_translator.translate.batch_translator import DEFAULT_BATCH_SIZE, DEFAULT_PROMPT_STRATEGY, PromptStrategy

logger = logging.getLogger(__name__)


class UnpackWorker(QThread):
    """把探测到的 Enigma Virtual Box 单文件游戏解包到一个新目录——大文件（真机见过
    1.5GB+）解包本身要跑一会，不能占着 GUI 线程。解包完不在这里判断"是不是识别到了
    RPG Maker 工程"，那是调用方（拿到 out_dir 后重新走一遍 detect_adapter）的事，
    这里只负责"解包这个动作本身有没有跑成功"。"""

    finished_ok = Signal(str)  # 解包出来的目录
    failed = Signal(str)

    def __init__(self, exe_path: Path, out_dir: Path, parent=None):
        super().__init__(parent)
        self._exe_path = exe_path
        self._out_dir = out_dir

    def run(self) -> None:
        try:
            unpack_evb(self._exe_path, self._out_dir)
        except Exception as e:
            logger.exception("EVB 解包失败")
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(str(self._out_dir))


class ExtractWorker(QThread):
    """从游戏工程提取文本到数据库。不需要 API Key，纯本地操作，跑在后台线程只是为了
    不卡住 UI（大工程提取可能有明显耗时）。"""

    finished_ok = Signal(int)  # unit count
    failed = Signal(str)

    def __init__(self, project_dir: Path, db_path: Path, parent=None):
        super().__init__(parent)
        self._project_dir = project_dir
        self._db_path = db_path

    def run(self) -> None:
        try:
            units = run_extract(self._project_dir, self._db_path)
        except Exception as e:
            logger.exception("提取失败")
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(len(units))


class TranslateWorker(QThread):
    """提取完成之后跑：translate。只负责翻译，结果落盘到 db_path（units.db），
    不碰游戏工程本身——和 inject 分开跑，是为了 inject 那一步（写文件、可能因为杀软/
    权限/磁盘问题失败）出错时，翻译结果已经稳稳存在 db 里，不用重新调用 API 重翻一遍。
    """

    stage_changed = Signal(str)
    progress_changed = Signal(int, int)  # (completed, total)
    finished_ok = Signal(int, list)  # (翻译完成的 TextUnit 条数, 失败条目 [(原文, 错误信息), ...])
    usage_changed = Signal(str, int, int)  # (model, prompt_tokens, completion_tokens)
    log_message = Signal(str)  # 请求重试/限流冷却/批次拆分/失败跳过等中间状态
    failed = Signal(str)

    def __init__(
        self,
        db_path: Path,
        api_key: str,
        base_url: str,
        model: str,
        concurrency: int,
        fallback_api_key: str | None = None,
        fallback_base_url: str | None = None,
        fallback_model: str | None = None,
        parent=None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        prompt_strategy: PromptStrategy = DEFAULT_PROMPT_STRATEGY,
        timeout: float = 60.0,
    ):
        super().__init__(parent)
        self._db_path = db_path
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._concurrency = concurrency
        self._fallback_api_key = fallback_api_key
        self._fallback_base_url = fallback_base_url
        self._fallback_model = fallback_model
        self._batch_size = batch_size
        self._prompt_strategy = prompt_strategy
        self._timeout = timeout
        # threading.Event 而不是 asyncio.Event：stop() 是主线程（Qt 事件循环）调这个
        # 方法，run() 里的 asyncio 事件循环跑在这个 QThread 自己的线程里，两边不共享
        # 一个 event loop，只有 threading.Event 能安全跨线程 set()。
        self._cancel_event = threading.Event()
        # on_log 在整个 provider 都被限流/挂掉的极端场景下，短时间内可能触发成百上
        # 千次（每条失败/每次重试/每次换 provider 各一条，批次失败还会级联拆分成更
        # 多子批各自重试）。main_window.py 那次"appendPlainText 撑爆崩溃"修的是 GUI
        # 侧渲染节流，但每次 emit 本身就是一次跨线程 Qt 排队事件——量一大，哪怕单个
        # slot 调用已经很便宜，光是把几千个事件挤进 GUI 线程的事件队列本身就会让
        # 界面卡得像"点了停止但完全没反应"（实际上 _cancel_event 这个 threading.Event
        # 从来没被这个队列挡住过，只是 GUI 表现上看不出来，用户体感等同于卡死）。
        # 这里在真正 emit 之前按固定间隔合并，从源头减少跨线程事件数量，而不是只在
        # 接收端节流渲染。
        self._log_buffer: list[str] = []
        self._log_buffer_lock = threading.Lock()
        self._last_log_emit_time = 0.0

    _LOG_EMIT_MIN_INTERVAL_SECONDS = 0.15

    def stop(self) -> None:
        """请求停止：不再派发新的翻译请求，已经发出去、正在等 API 响应的请求也会被
        主动打断（见 translate/batch_translator.py 的 _chat_cancellable），不会傻等
        它们自然跑完继续烧 token。被打断的条目保留 pending，随时能继续翻剩下的。"""
        self._cancel_event.set()

    def _buffered_on_log(self, message: str) -> None:
        with self._log_buffer_lock:
            self._log_buffer.append(message)
            now = time.monotonic()
            if now - self._last_log_emit_time < self._LOG_EMIT_MIN_INTERVAL_SECONDS:
                return
            batched = "\n".join(self._log_buffer)
            self._log_buffer.clear()
            self._last_log_emit_time = now
        self.log_message.emit(batched)

    def _flush_log_buffer(self) -> None:
        with self._log_buffer_lock:
            if not self._log_buffer:
                return
            batched = "\n".join(self._log_buffer)
            self._log_buffer.clear()
            self._last_log_emit_time = time.monotonic()
        self.log_message.emit(batched)

    def run(self) -> None:
        try:
            self.stage_changed.emit("翻译中…")
            translated, failures = asyncio.run(
                run_translate(
                    self._db_path,
                    self._api_key,
                    self._base_url,
                    self._model,
                    self._concurrency,
                    on_progress=self.progress_changed.emit,
                    fallback_api_key=self._fallback_api_key,
                    fallback_base_url=self._fallback_base_url,
                    fallback_model=self._fallback_model,
                    cancel_check=self._cancel_event.is_set,
                    on_usage=self.usage_changed.emit,
                    batch_size=self._batch_size,
                    prompt_strategy=self._prompt_strategy,
                    timeout=self._timeout,
                    on_log=self._buffered_on_log,
                )
            )
        except Exception as e:
            # 汉化流程里任何异常都要传回 GUI 展示，不能让后台线程静默崩溃/退出
            logger.exception("翻译失败")
            self._flush_log_buffer()
            self.failed.emit(str(e))
            return
        self._flush_log_buffer()
        self.finished_ok.emit(len(translated), failures)


class InjectWorker(QThread):
    """把 db_path 里已翻译的内容写回游戏工程。不需要 API Key、不需要重新翻译——
    可以在 TranslateWorker 跑完后随时重试（比如上次写盘失败了，或者用户想换个
    输出目录再导一次），已翻译的内容留在 db 里不会因为这一步失败而丢。
    """

    finished_ok = Signal(int, str)  # (处理的 TextUnit 条数, 输出目录)
    failed = Signal(str)

    def __init__(self, project_dir: Path, db_path: Path, output_dir: Path, parent=None):
        super().__init__(parent)
        self._project_dir = project_dir
        self._db_path = db_path
        self._output_dir = output_dir

    def run(self) -> None:
        try:
            units = run_inject(self._project_dir, self._db_path, self._output_dir)
        except Exception as e:
            logger.exception("注入失败")
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(len(units), str(self._output_dir))
