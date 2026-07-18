from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from rpg_translator.core.pipeline import run_extract, run_glossary, run_inject, run_translate
from rpg_translator.translate.batch_translator import DEFAULT_BATCH_SIZE

logger = logging.getLogger(__name__)


class ExtractAndGlossaryWorker(QThread):
    """extract + 术语候选抽取。完成后交回主线程弹术语表确认框——批量翻译前必须
    经用户确认/编辑术语表才能继续（见 spec 第 10 节），所以这一步和翻译分成两个线程。

    fallback_* 三个参数由 main_window.py 统一解析好再传进来（GUI 设置里填了就用
    GUI 的，没填就退回 .env 默认值），worker 自己不碰 Settings()，避免两处各解析
    一遍导致 GUI 配置和实际生效的值对不上。
    """

    finished_ok = Signal(dict, int)  # (glossary candidates, unit count)
    usage_changed = Signal(str, int, int)  # (model, prompt_tokens, completion_tokens)
    failed = Signal(str)

    def __init__(
        self,
        project_dir: Path,
        db_path: Path,
        api_key: str,
        base_url: str,
        model: str,
        fallback_api_key: str | None = None,
        fallback_base_url: str | None = None,
        fallback_model: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._project_dir = project_dir
        self._db_path = db_path
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._fallback_api_key = fallback_api_key
        self._fallback_base_url = fallback_base_url
        self._fallback_model = fallback_model

    def run(self) -> None:
        try:
            units = run_extract(self._project_dir, self._db_path)
            candidates = asyncio.run(
                run_glossary(
                    self._db_path,
                    self._api_key,
                    self._base_url,
                    self._model,
                    self._fallback_api_key,
                    self._fallback_base_url,
                    self._fallback_model,
                    on_usage=self.usage_changed.emit,
                )
            )
        except Exception as e:
            logger.exception("提取/术语抽取失败")
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(candidates, len(units))


class TranslateWorker(QThread):
    """用户确认术语表之后跑：translate。只负责翻译，结果落盘到 db_path（units.db），
    不碰游戏工程本身——和 inject 分开跑，是为了 inject 那一步（写文件、可能因为杀软/
    权限/磁盘问题失败）出错时，翻译结果已经稳稳存在 db 里，不用重新调用 API 重翻一遍。
    """

    stage_changed = Signal(str)
    progress_changed = Signal(int, int)  # (completed, total)
    finished_ok = Signal(int, list)  # (翻译完成的 TextUnit 条数, 失败条目 [(原文, 错误信息), ...])
    usage_changed = Signal(str, int, int)  # (model, prompt_tokens, completion_tokens)
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
        # threading.Event 而不是 asyncio.Event：stop() 是主线程（Qt 事件循环）调这个
        # 方法，run() 里的 asyncio 事件循环跑在这个 QThread 自己的线程里，两边不共享
        # 一个 event loop，只有 threading.Event 能安全跨线程 set()。
        self._cancel_event = threading.Event()

    def stop(self) -> None:
        """请求停止：不再派发新的翻译请求，已经发出去、正在等 API 响应的请求也会被
        主动打断（见 translate/batch_translator.py 的 _chat_cancellable），不会傻等
        它们自然跑完继续烧 token。被打断的条目保留 pending，随时能继续翻剩下的。"""
        self._cancel_event.set()

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
                )
            )
        except Exception as e:
            # 汉化流程里任何异常都要传回 GUI 展示，不能让后台线程静默崩溃/退出
            logger.exception("翻译失败")
            self.failed.emit(str(e))
            return
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
