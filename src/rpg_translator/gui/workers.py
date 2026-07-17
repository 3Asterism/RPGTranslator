from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from rpg_translator.core.pipeline import run_extract, run_glossary, run_inject, run_translate


class ExtractAndGlossaryWorker(QThread):
    """extract + 术语候选抽取。完成后交回主线程弹术语表确认框——批量翻译前必须
    经用户确认/编辑术语表才能继续（见 spec 第 10 节），所以这一步和翻译分成两个线程。
    """

    finished_ok = Signal(dict, int)  # (glossary candidates, unit count)
    failed = Signal(str)

    def __init__(
        self,
        project_dir: Path,
        db_path: Path,
        api_key: str,
        base_url: str,
        model: str,
        parent=None,
    ):
        super().__init__(parent)
        self._project_dir = project_dir
        self._db_path = db_path
        self._api_key = api_key
        self._base_url = base_url
        self._model = model

    def run(self) -> None:
        try:
            units = run_extract(self._project_dir, self._db_path)
            candidates = asyncio.run(
                run_glossary(self._db_path, self._api_key, self._base_url, self._model)
            )
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(candidates, len(units))


class TranslateAndInjectWorker(QThread):
    """用户确认术语表之后跑：translate -> inject。"""

    stage_changed = Signal(str)
    progress_changed = Signal(int, int)  # (completed, total)
    finished_ok = Signal(int, str)  # (处理的 TextUnit 条数, 输出目录)
    failed = Signal(str)

    def __init__(
        self,
        project_dir: Path,
        db_path: Path,
        output_dir: Path,
        api_key: str,
        base_url: str,
        model: str,
        concurrency: int,
        parent=None,
    ):
        super().__init__(parent)
        self._project_dir = project_dir
        self._db_path = db_path
        self._output_dir = output_dir
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._concurrency = concurrency

    def run(self) -> None:
        try:
            self.stage_changed.emit("翻译中…")
            asyncio.run(
                run_translate(
                    self._db_path,
                    self._api_key,
                    self._base_url,
                    self._model,
                    self._concurrency,
                    on_progress=self.progress_changed.emit,
                )
            )
            self.stage_changed.emit("写回中…")
            units = run_inject(self._project_dir, self._db_path, self._output_dir)
        except Exception as e:
            # 汉化流程里任何异常都要传回 GUI 展示，不能让后台线程静默崩溃/退出
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(len(units), str(self._output_dir))
