from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.core.pipeline import UnknownEngineError, detect_adapter
from rpg_translator.engines.base import EngineAdapter
from rpg_translator.gui.glossary_dialog import GlossaryDialog
from rpg_translator.gui.settings_dialog import APP_NAME, ORG_NAME, SettingsDialog
from rpg_translator.gui.workers import ExtractAndGlossaryWorker, TranslateAndInjectWorker

_ENGINE_LABELS = {
    "mv": "RPG Maker MV",
    "mz": "RPG Maker MZ",
    "vxace": "RPG Maker VX Ace",
    "xp": "RPG Maker XP",
    "vx": "RPG Maker VX",
}


def resolve_dropped_path(path: Path) -> Path:
    """拖 exe 时自动定位到其所在目录，拖文件夹则原样返回。"""
    return path.parent if path.is_file() else path


class DropArea(QFrame):
    """接受拖入游戏文件夹或 Game.exe（拖 exe 时自动定位到其所在目录）。"""

    path_dropped = Signal(Path)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(120)
        layout = QVBoxLayout(self)
        label = QLabel("将游戏文件夹或 Game.exe 拖到这里")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        path = Path(urls[0].toLocalFile())
        self.path_dropped.emit(resolve_dropped_path(path))


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("RPG Maker 汉化工具")
        self.resize(640, 480)

        self._project_dir: Path | None = None
        self._adapter: EngineAdapter | None = None
        self._db_path: Path | None = None
        self._output_dir: str | None = None
        self._extract_glossary_worker: ExtractAndGlossaryWorker | None = None
        self._translate_inject_worker: TranslateAndInjectWorker | None = None

        self._drop_area = DropArea()
        self._drop_area.path_dropped.connect(self._on_path_dropped)

        self._info_label = QLabel("尚未选择游戏工程")

        self._start_button = QPushButton("开始汉化")
        self._start_button.setEnabled(False)
        self._start_button.clicked.connect(self._on_start_clicked)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)

        self._open_output_button = QPushButton("打开输出文件夹")
        self._open_output_button.setVisible(False)
        self._open_output_button.clicked.connect(self._on_open_output_clicked)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self._drop_area)
        layout.addWidget(self._info_label)
        layout.addWidget(self._start_button)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._log)
        layout.addWidget(self._open_output_button)
        self.setCentralWidget(central)

        file_menu = self.menuBar().addMenu("文件")
        settings_action = file_menu.addAction("设置")
        settings_action.triggered.connect(self._open_settings)

    def _log_message(self, message: str) -> None:
        self._log.appendPlainText(message)

    def _on_path_dropped(self, path: Path) -> None:
        self._project_dir = path
        try:
            adapter = detect_adapter(path)
        except UnknownEngineError:
            self._info_label.setText("未识别到支持的 RPG Maker 引擎")
            self._adapter = None
            self._start_button.setEnabled(False)
            return

        try:
            units = adapter.extract(path)
        except Exception as e:
            self._info_label.setText(f"扫描失败：{e}")
            self._adapter = None
            self._start_button.setEnabled(False)
            return

        self._adapter = adapter
        engine_label = _ENGINE_LABELS.get(adapter.engine_name, adapter.engine_name)
        self._info_label.setText(f"识别到引擎：{engine_label}，扫描到文本约 {len(units)} 条")
        self._start_button.setEnabled(True)

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _on_start_clicked(self) -> None:
        if self._project_dir is None:
            return
        api_key = get_deepseek_api_key()
        if not api_key:
            QMessageBox.warning(self, "未配置 API Key", "请先在设置里配置 DeepSeek API Key。")
            return

        qsettings = QSettings(ORG_NAME, APP_NAME)
        model = str(qsettings.value("model", "deepseek-v4-flash"))
        base_url = Settings().deepseek_base_url

        self._db_path = self._project_dir / ".rpg_translator" / "units.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._start_button.setEnabled(False)
        self._open_output_button.setVisible(False)
        self._progress_bar.setRange(0, 0)  # 不确定进度，先用忙碌样式
        self._log_message("提取中…")

        self._extract_glossary_worker = ExtractAndGlossaryWorker(
            self._project_dir, self._db_path, api_key, base_url, model
        )
        self._extract_glossary_worker.finished_ok.connect(self._on_extract_glossary_done)
        self._extract_glossary_worker.failed.connect(self._on_failed)
        self._extract_glossary_worker.start()

    def _on_extract_glossary_done(self, candidates: dict, unit_count: int) -> None:
        self._log_message(f"术语抽取完成：{len(candidates)} 条候选")
        self._progress_bar.setRange(0, 100)

        dialog = GlossaryDialog(self._db_path, candidates, self)
        if dialog.exec() != GlossaryDialog.DialogCode.Accepted:
            self._log_message("已取消。")
            self._reset_after_run()
            return

        qsettings = QSettings(ORG_NAME, APP_NAME)
        model = str(qsettings.value("model", "deepseek-v4-flash"))
        concurrency = int(qsettings.value("concurrency", 4))
        output_dir = Path(str(qsettings.value("output_dir", "output")))
        base_url = Settings().deepseek_base_url
        api_key = get_deepseek_api_key()

        self._translate_inject_worker = TranslateAndInjectWorker(
            self._project_dir,
            self._db_path,
            output_dir,
            api_key,
            base_url,
            model,
            concurrency,
        )
        self._translate_inject_worker.stage_changed.connect(self._log_message)
        self._translate_inject_worker.progress_changed.connect(self._on_progress_changed)
        self._translate_inject_worker.finished_ok.connect(self._on_translate_inject_done)
        self._translate_inject_worker.failed.connect(self._on_failed)
        self._translate_inject_worker.start()

    def _on_progress_changed(self, completed: int, total: int) -> None:
        self._progress_bar.setRange(0, max(total, 1))
        self._progress_bar.setValue(completed)
        self._log_message(f"翻译批次 {completed}/{total}")

    def _on_translate_inject_done(self, unit_count: int, output_dir: str) -> None:
        self._output_dir = output_dir
        self._log_message(f"完成：{unit_count} 条文本，输出到 {output_dir}")
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)
        self._open_output_button.setVisible(True)
        QMessageBox.information(self, "汉化完成", f"输出目录：{output_dir}")
        self._reset_after_run()

    def _on_failed(self, message: str) -> None:
        self._log_message(f"出错：{message}")
        QMessageBox.critical(self, "出错", message)
        self._reset_after_run()

    def _reset_after_run(self) -> None:
        self._start_button.setEnabled(self._adapter is not None)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)

    def _on_open_output_clicked(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_dir))
