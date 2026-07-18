from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rpg_translator.config import get_deepseek_api_key
from rpg_translator.core.pipeline import (
    UnknownEngineError,
    detect_adapter,
    export_translation_package,
    has_language_variant,
    import_translation_package,
    run_extract,
    switch_language,
)
from rpg_translator.engines.base import EngineAdapter
from rpg_translator.gui.settings_dialog import (
    APP_NAME,
    ENGINE_LOCAL,
    ORG_NAME,
    SettingsDialog,
    resolve_base_url,
    resolve_fallback_config,
    resolve_local_config,
)
from rpg_translator.gui.workers import ExtractWorker, InjectWorker, TranslateWorker
from rpg_translator.translate.batch_translator import DEFAULT_BATCH_SIZE, DEFAULT_PROMPT_STRATEGY
from rpg_translator.translate.pricing import estimate_cost_cny
from rpg_translator.translate.sakura_prompt import SAKURA_PROMPT_STRATEGY

logger = logging.getLogger(__name__)

_ENGINE_LABELS = {
    "mv": "RPG Maker MV",
    "mz": "RPG Maker MZ",
    "vxace": "RPG Maker VX Ace",
    "xp": "RPG Maker XP",
    "vx": "RPG Maker VX",
}

# 手写 QSS，不引入 qt-material 之类的第三方主题库——保持 PyInstaller 打包体积和
# 依赖面不变。配色走清爽的浅色卡片风格，参考常见开源翻译/本地化工具（如
# Translator++、MTool 系工具）的分区块 + 强调色按钮布局。
#
# 在 QApplication 级别应用（见 gui/app.py），不是只在 MainWindow 上 setStyleSheet：
# QComboBox 的下拉列表、QMessageBox 之类的顶层弹出窗口不会继承父 widget 的样式表，
# 只有应用级样式表才能盖到它们。同时给 QLineEdit/QComboBox/QSpinBox 显式定义浅色
# 背景——不然这几个控件的背景色会跟随系统主题（深色模式下是暗色背景），叠加这份
# 样式表里给 QWidget 强制设的深色文字，会变成一片看不清文字的黑底方块。
APP_STYLESHEET = """
QMainWindow, QDialog {
    background: #f4f6f9;
}
QWidget {
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #1f2430;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #e2e6ed;
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px 12px 12px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #4d5566;
}
QFrame#dropArea {
    background: #eef2ff;
    border: 2px dashed #aab6e8;
    border-radius: 10px;
}
QFrame#dropArea[active="true"] {
    border-color: #5468e0;
    background: #e4e9ff;
}
QLabel#dropLabel {
    color: #5468e0;
    font-size: 14px;
    font-weight: 600;
}
QLabel#infoLabel {
    color: #626b7d;
    padding: 2px 2px;
}
QLabel#windowTitleLabel {
    color: #1f2430;
    font-size: 16px;
    font-weight: 700;
}
QPushButton {
    background: #5468e0;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 18px;
    font-weight: 600;
}
QPushButton:hover:!disabled {
    background: #4655c4;
}
QPushButton:disabled {
    background: #c7cce0;
    color: #8b91a6;
}
QPushButton#secondaryButton {
    background: #eef0f7;
    color: #3c4257;
}
QPushButton#secondaryButton:hover:!disabled {
    background: #dfe3f0;
}
QProgressBar {
    border: 1px solid #e2e6ed;
    border-radius: 6px;
    background: #eef0f7;
    text-align: center;
    height: 18px;
}
QProgressBar::chunk {
    background: #5468e0;
    border-radius: 5px;
}
QPlainTextEdit {
    background: #1f2430;
    color: #d7dcf0;
    border-radius: 8px;
    padding: 8px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
}
QLineEdit, QComboBox, QSpinBox {
    background: #ffffff;
    color: #1f2430;
    border: 1px solid #d7dbe4;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #5468e0;
    selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #5468e0;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    background: #f4f6f9;
    color: #9aa1b2;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    color: #1f2430;
    border: 1px solid #d7dbe4;
    outline: none;
    selection-background-color: #5468e0;
    selection-color: #ffffff;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 16px;
    border-left: 1px solid #d7dbe4;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f7f8fc;
    gridline-color: #e2e6ed;
    border: 1px solid #e2e6ed;
    border-radius: 8px;
    selection-background-color: #5468e0;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #eef0f7;
    color: #3c4257;
    padding: 6px;
    border: none;
    border-bottom: 1px solid #e2e6ed;
    font-weight: 600;
}
QDialogButtonBox QPushButton {
    min-width: 76px;
}
QCheckBox {
    spacing: 6px;
}
QStatusBar {
    background: #f4f6f9;
    border-top: 1px solid #e2e6ed;
}
QLabel#usageLabel {
    color: #626b7d;
    padding: 2px 6px;
}
"""


def resolve_dropped_path(path: Path) -> Path:
    """拖 exe 时自动定位到其所在目录，拖文件夹则原样返回。"""
    return path.parent if path.is_file() else path


def db_path_for_project(project_dir: Path) -> Path:
    return project_dir / ".rpg_translator" / "units.db"


def default_output_dir(project_dir: Path) -> Path:
    """默认输出到工程同级目录下的 `<工程名>_汉化`，而不是当前工作目录下一个裸的
    `output` 文件夹——双击 exe 打包版时 cwd 是谁都不知道，裸相对路径对用户很不友好。"""
    return project_dir.parent / f"{project_dir.name}_汉化"


class DropArea(QFrame):
    """接受拖入游戏文件夹或 Game.exe（拖 exe 时自动定位到其所在目录）。"""

    path_dropped = Signal(Path)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("dropArea")
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(110)
        layout = QVBoxLayout(self)
        label = QLabel("将游戏文件夹或 Game.exe 拖到这里")
        label.setObjectName("dropLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("active", "true")
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001 - Qt 事件参数类型不必标注
        self.setProperty("active", "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setProperty("active", "false")
        self.style().unpolish(self)
        self.style().polish(self)
        urls = event.mimeData().urls()
        if not urls:
            return
        path = Path(urls[0].toLocalFile())
        self.path_dropped.emit(resolve_dropped_path(path))


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("RPG Maker 汉化工具")
        self.resize(760, 620)
        # 样式表在 QApplication 级别应用（见 gui/app.py 的 APP_STYLESHEET），这里不用
        # 再单独 setStyleSheet 一遍——应用级样式表本来就会级联到这个窗口和它的子控件。

        self._project_dir: Path | None = None
        self._adapter: EngineAdapter | None = None
        self._db_path: Path | None = None
        self._output_dir: str | None = None
        self._extract_worker: ExtractWorker | None = None
        self._translate_worker: TranslateWorker | None = None
        self._inject_worker: InjectWorker | None = None

        # 本次会话（软件跑起来到现在）累计的 token 用量/预估花费——重开软件清零，
        # 不落地存储，纯粹是给正在跑的这一轮汉化一个实时的量级参考。
        self._session_prompt_tokens = 0
        self._session_completion_tokens = 0
        self._session_cost_cny = 0.0
        self._session_has_unpriced_model = False

        self._title_label = QLabel("RPG Maker 汉化工具")
        self._title_label.setObjectName("windowTitleLabel")

        self._settings_button = QPushButton("⚙ 设置")
        self._settings_button.setObjectName("secondaryButton")
        self._settings_button.clicked.connect(self._open_settings)

        header_row = QHBoxLayout()
        header_row.addWidget(self._title_label)
        header_row.addStretch(1)
        header_row.addWidget(self._settings_button)

        self._drop_area = DropArea()
        self._drop_area.path_dropped.connect(self._on_path_dropped)

        self._info_label = QLabel("尚未选择游戏工程")
        self._info_label.setObjectName("infoLabel")

        project_box = QGroupBox("1. 选择游戏工程")
        project_layout = QVBoxLayout(project_box)
        project_layout.addWidget(self._drop_area)
        project_layout.addWidget(self._info_label)

        self._start_button = QPushButton("开始翻译")
        self._start_button.setEnabled(False)
        self._start_button.clicked.connect(self._on_start_clicked)

        self._stop_button = QPushButton("停止")
        self._stop_button.setObjectName("secondaryButton")
        self._stop_button.setVisible(False)
        self._stop_button.clicked.connect(self._on_stop_clicked)

        self._retry_failed_button = QPushButton("重试失败项")
        self._retry_failed_button.setObjectName("secondaryButton")
        self._retry_failed_button.setVisible(False)
        self._retry_failed_button.clicked.connect(self._on_retry_failed_clicked)

        start_row = QHBoxLayout()
        start_row.addWidget(self._start_button)
        start_row.addWidget(self._stop_button)
        start_row.addWidget(self._retry_failed_button)
        start_row.addStretch(1)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(160)

        translate_box = QGroupBox("2. 翻译（后台跑，结果先存本地，不动游戏文件）")
        translate_layout = QVBoxLayout(translate_box)
        translate_layout.addLayout(start_row)
        translate_layout.addWidget(self._progress_bar)
        translate_layout.addWidget(self._log)

        self._load_translated_button = QPushButton("选择已翻译工程…")
        self._load_translated_button.setObjectName("secondaryButton")
        self._load_translated_button.clicked.connect(self._on_load_translated_project_clicked)

        self._output_dir_edit = QLineEdit()
        self._output_dir_edit.setPlaceholderText("翻译后的游戏工程输出到这里")
        browse_output_button = QPushButton("浏览…")
        browse_output_button.setObjectName("secondaryButton")
        browse_output_button.clicked.connect(self._browse_output_dir)

        output_dir_row = QHBoxLayout()
        output_dir_row.addWidget(QLabel("输出目录:"))
        output_dir_row.addWidget(self._output_dir_edit, stretch=1)
        output_dir_row.addWidget(browse_output_button)

        self._inject_button = QPushButton("注入到游戏")
        self._inject_button.setEnabled(False)
        self._inject_button.clicked.connect(self._on_inject_clicked)

        self._open_output_button = QPushButton("打开输出文件夹")
        self._open_output_button.setObjectName("secondaryButton")
        self._open_output_button.setVisible(False)
        self._open_output_button.clicked.connect(self._on_open_output_clicked)

        inject_row = QHBoxLayout()
        inject_row.addWidget(self._load_translated_button)
        inject_row.addWidget(self._inject_button)
        inject_row.addWidget(self._open_output_button)
        inject_row.addStretch(1)

        inject_box = QGroupBox("3. 注入（把已翻译内容写回游戏工程，和翻译分开跑——写盘失败可以直接重试，不用重新翻译）")
        inject_layout = QVBoxLayout(inject_box)
        inject_layout.addLayout(output_dir_row)
        inject_layout.addLayout(inject_row)

        self._switch_original_button = QPushButton("切换为原文")
        self._switch_original_button.setObjectName("secondaryButton")
        self._switch_original_button.setEnabled(False)
        self._switch_original_button.clicked.connect(lambda: self._on_switch_language("original"))

        self._switch_translated_button = QPushButton("切换为译文")
        self._switch_translated_button.setObjectName("secondaryButton")
        self._switch_translated_button.setEnabled(False)
        self._switch_translated_button.clicked.connect(lambda: self._on_switch_language("translated"))

        self._export_package_button = QPushButton("导出翻译包…")
        self._export_package_button.setObjectName("secondaryButton")
        self._export_package_button.clicked.connect(self._on_export_package_clicked)

        self._import_package_button = QPushButton("导入翻译包…")
        self._import_package_button.setObjectName("secondaryButton")
        self._import_package_button.clicked.connect(self._on_import_package_clicked)

        share_row = QHBoxLayout()
        share_row.addWidget(self._switch_original_button)
        share_row.addWidget(self._switch_translated_button)
        share_row.addWidget(self._export_package_button)
        share_row.addWidget(self._import_package_button)
        share_row.addStretch(1)

        share_box = QGroupBox(
            "4. 中日对照 / 分享给他人（切换只影响输出目录里的文本文件，不影响素材；"
            "翻译包是可分享的译文数据，不含游戏本体，对方同版本游戏可直接导入复用）"
        )
        share_layout = QVBoxLayout(share_box)
        share_layout.addLayout(share_row)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addLayout(header_row)
        layout.addWidget(project_box)
        layout.addWidget(translate_box, stretch=1)
        layout.addWidget(inject_box)
        layout.addWidget(share_box)
        self.setCentralWidget(central)

        self._usage_label = QLabel("本次会话：暂无 token 用量")
        self._usage_label.setObjectName("usageLabel")
        self.statusBar().addWidget(self._usage_label)

    def _log_message(self, message: str) -> None:
        self._log.appendPlainText(message)
        logger.info(message)  # 同步落一份到文件日志，软件本身崩了也能翻记录复盘

    def _on_usage_changed(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        self._session_prompt_tokens += prompt_tokens
        self._session_completion_tokens += completion_tokens
        cost = estimate_cost_cny(model, prompt_tokens, completion_tokens)
        if cost is None:
            self._session_has_unpriced_model = True
        else:
            self._session_cost_cny += cost
        self._refresh_usage_label()

    def _refresh_usage_label(self) -> None:
        total = self._session_prompt_tokens + self._session_completion_tokens
        text = (
            f"本次会话 tokens：输入 {self._session_prompt_tokens:,} / "
            f"输出 {self._session_completion_tokens:,}（共 {total:,}） · "
            f"预估花费 ¥{self._session_cost_cny:.2f}"
        )
        if self._session_has_unpriced_model:
            text += "（含未知计价模型，费用为部分预估，仅供参考）"
        self._usage_label.setText(text)

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
        try:
            resume_note = self._resume_progress_note(path, units)
        except Exception:
            # 断点续传进度只是锦上添花的提示，读取失败（比如上次的 db 文件损坏/被占用）
            # 不该拦住整个拖拽识别流程——记下日志，提示照常显示，只是不带续译进度。
            logger.exception("读取续译进度失败：%s", path)
            resume_note = ""
        self._info_label.setText(
            f"识别到引擎：{engine_label}，扫描到文本约 {len(units)} 条{resume_note}"
        )
        self._start_button.setEnabled(True)
        self._inject_button.setEnabled(False)
        self._open_output_button.setVisible(False)
        self._retry_failed_button.setVisible(False)
        if not self._output_dir_edit.text().strip():
            self._output_dir_edit.setText(str(default_output_dir(path)))

    @staticmethod
    def _resume_progress_note(project_dir: Path, units: list) -> str:
        """如果这个工程之前已经翻译过一部分（db 文件存在），提示已完成的进度——
        断点续传对用户可见，不用重新点了"开始翻译"才发现原来接着上次的进度在跑。"""
        db_path = db_path_for_project(project_dir)
        if not db_path.is_file():
            return ""

        from rpg_translator.core.store import Store

        with Store(db_path) as store:
            done_ids = {u.id for u in store.list_units() if u.status != "pending"}
        done = sum(1 for u in units if u.id in done_ids)
        if done == 0:
            return ""
        return f"，已翻译 {done}/{len(units)}（点击「开始翻译」续译剩余部分）"

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _on_start_clicked(self) -> None:
        if self._project_dir is None:
            return
        qsettings = QSettings(ORG_NAME, APP_NAME)
        if qsettings.value("engine", "online") == ENGINE_LOCAL:
            _, local_base_url, local_model = resolve_local_config(qsettings)
            if not local_base_url or not local_model:
                QMessageBox.warning(
                    self, "未配置本地模型",
                    "请先在设置里配置本地模型的 Base URL 和模型名。",
                )
                return
        else:
            api_key = get_deepseek_api_key()
            if not api_key:
                QMessageBox.warning(self, "未配置 API Key", "请先在设置里配置 DeepSeek API Key。")
                return

        self._db_path = db_path_for_project(self._project_dir)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._start_button.setEnabled(False)
        self._stop_button.setVisible(False)
        self._inject_button.setEnabled(False)
        self._open_output_button.setVisible(False)
        self._retry_failed_button.setVisible(False)
        self._progress_bar.setRange(0, 0)  # 不确定进度，先用忙碌样式
        self._log_message("提取中…")

        self._extract_worker = ExtractWorker(self._project_dir, self._db_path)
        self._extract_worker.finished_ok.connect(self._on_extract_done)
        self._extract_worker.failed.connect(self._on_failed)
        self._extract_worker.start()

    def _on_extract_done(self, unit_count: int) -> None:
        self._log_message(f"提取完成：{unit_count} 条文本")
        self._progress_bar.setRange(0, 100)
        self._start_translate_worker()

    def _start_translate_worker(self) -> None:
        """起一个 TranslateWorker 翻译 db 里当前的 pending 条目——只翻译，不重新走
        提取。首次翻译（提取完成后）和「重试失败项」（失败条目还是 pending，直接
        重跑这一步就够）都调用这个方法，避免两处各写一遍起 worker 的逻辑。
        """
        qsettings = QSettings(ORG_NAME, APP_NAME)
        concurrency = int(qsettings.value("concurrency", 4))
        batch_size = int(qsettings.value("batch_size", DEFAULT_BATCH_SIZE))

        if qsettings.value("engine", "online") == ENGINE_LOCAL:
            # 本地模型走专门适配过的 prompt 模板（见 sakura_prompt.py），不走 DeepSeek
            # 那套自由格式；也不启用备用 provider——故障转移是为云端服务瞬时报错设计
            # 的，本地服务连不上通常是配置错了，切去 DeepSeek 反而会误导排查方向。
            api_key, base_url, model = resolve_local_config(qsettings)
            fallback_api_key = fallback_base_url = fallback_model = None
            prompt_strategy = SAKURA_PROMPT_STRATEGY
        else:
            model = str(qsettings.value("model", "deepseek-v4-flash"))
            base_url = resolve_base_url(qsettings)
            fallback_api_key, fallback_base_url, fallback_model = resolve_fallback_config(qsettings)
            api_key = get_deepseek_api_key()
            prompt_strategy = DEFAULT_PROMPT_STRATEGY

        self._retry_failed_button.setVisible(False)
        self._translate_worker = TranslateWorker(
            self._db_path,
            api_key,
            base_url,
            model,
            concurrency,
            fallback_api_key,
            fallback_base_url,
            fallback_model,
            batch_size=batch_size,
            prompt_strategy=prompt_strategy,
        )
        self._translate_worker.stage_changed.connect(self._log_message)
        self._translate_worker.progress_changed.connect(self._on_progress_changed)
        self._translate_worker.finished_ok.connect(self._on_translate_done)
        self._translate_worker.usage_changed.connect(self._on_usage_changed)
        self._translate_worker.failed.connect(self._on_failed)
        self._translate_worker.start()
        self._start_button.setEnabled(False)
        self._stop_button.setVisible(True)
        self._stop_button.setEnabled(True)

    def _on_progress_changed(self, completed: int, total: int) -> None:
        self._progress_bar.setRange(0, max(total, 1))
        self._progress_bar.setValue(completed)
        self._log_message(f"翻译批次 {completed}/{total}")

    def _on_translate_done(self, unit_count: int, failures: list[tuple[str, str]]) -> None:
        self._log_message(f"翻译完成，共 {unit_count} 条，可以点击下方“注入到游戏”写回。")
        if failures:
            self._log_message(
                f"{len(failures)} 条翻译失败已跳过（保留待译状态，点「重试失败项」"
                "或重新点「开始翻译」可续译）："
            )
            for source_text, error in failures[:10]:
                preview = source_text[:30].replace("\n", " ")
                self._log_message(f"  - {preview!r}: {error}")
            if len(failures) > 10:
                self._log_message(f"  ……其余 {len(failures) - 10} 条略")
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)
        self._start_button.setEnabled(True)
        self._inject_button.setEnabled(True)
        self._stop_button.setVisible(False)
        self._retry_failed_button.setVisible(bool(failures))

    def _on_retry_failed_clicked(self) -> None:
        """失败条目还保留着 status="pending"，直接重跑翻译这一步就够——不用再走一遍
        提取（工程文本也没变）。"""
        self._log_message("重试失败项…")
        self._start_translate_worker()

    def _on_stop_clicked(self) -> None:
        if self._translate_worker is None:
            return
        self._stop_button.setEnabled(False)
        self._log_message("正在停止（等待当前批次的请求跑完落盘）…")
        self._translate_worker.stop()

    def _browse_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择输出目录", self._output_dir_edit.text()
        )
        if directory:
            self._output_dir_edit.setText(directory)

    def _on_load_translated_project_clicked(self) -> None:
        """跳过提取/翻译，直接选一个之前翻译过的游戏工程目录，注入它已经存在
        db 里的翻译结果——关掉软件重开、或者上次注入失败想换个目录重试，都不用
        再走一遍提取+翻译。"""
        directory = QFileDialog.getExistingDirectory(self, "选择已翻译的游戏工程目录")
        if not directory:
            return
        self._load_translated_project(Path(directory))

    def _load_translated_project(self, project_dir: Path) -> None:
        db_path = db_path_for_project(project_dir)
        if not db_path.is_file():
            QMessageBox.warning(
                self,
                "未找到翻译记录",
                f"{project_dir} 下没有找到 .rpg_translator/units.db，"
                "请先用上面的拖拽区域跑一遍翻译。",
            )
            return

        from rpg_translator.core.store import Store

        with Store(db_path) as store:
            translated_count = len(store.list_units(status="translated"))

        self._project_dir = project_dir
        self._db_path = db_path
        self._info_label.setText(f"已加载翻译记录：{project_dir}，共 {translated_count} 条已翻译")
        if not self._output_dir_edit.text().strip():
            self._output_dir_edit.setText(str(default_output_dir(project_dir)))
        self._inject_button.setEnabled(True)
        self._open_output_button.setVisible(False)
        self._log_message(f"已加载 {project_dir}，可以直接点击“注入到游戏”。")

    def _on_inject_clicked(self) -> None:
        if self._project_dir is None or self._db_path is None:
            return

        output_dir_text = self._output_dir_edit.text().strip()
        if not output_dir_text:
            QMessageBox.warning(self, "未选择输出目录", "请先填写或浏览选择一个输出目录。")
            return
        output_dir = Path(output_dir_text)

        qsettings = QSettings(ORG_NAME, APP_NAME)
        qsettings.setValue("output_dir", output_dir_text)

        self._inject_button.setEnabled(False)
        self._log_message("写回中…")

        self._inject_worker = InjectWorker(self._project_dir, self._db_path, output_dir)
        self._inject_worker.finished_ok.connect(self._on_inject_done)
        self._inject_worker.failed.connect(self._on_inject_failed)
        self._inject_worker.start()

    def _on_inject_done(self, unit_count: int, output_dir: str) -> None:
        self._output_dir = output_dir
        self._log_message(f"注入完成：{unit_count} 条文本，输出到 {output_dir}")
        self._open_output_button.setVisible(True)
        self._inject_button.setEnabled(True)
        output_path = Path(output_dir)
        self._switch_original_button.setEnabled(has_language_variant(output_path, "original"))
        self._switch_translated_button.setEnabled(has_language_variant(output_path, "translated"))
        QMessageBox.information(self, "汉化完成", f"输出目录：{output_dir}")

    def _on_inject_failed(self, message: str) -> None:
        # 注入失败不影响已经翻译好、存在 db 里的内容——按钮保持可用，改改输出目录
        # 或者解决权限/占用问题后可以直接再点一次重试，不用重新走一遍翻译。
        self._log_message(f"注入出错：{message}")
        self._inject_button.setEnabled(True)
        QMessageBox.critical(self, "注入出错", message)

    def _on_switch_language(self, variant: str) -> None:
        if not self._output_dir:
            return
        try:
            count = switch_language(Path(self._output_dir), variant)
        except FileNotFoundError as e:
            QMessageBox.warning(self, "切换失败", str(e))
            return
        label = "原文" if variant == "original" else "译文"
        self._log_message(f"已切换为{label}：{count} 个文件。")

    def _on_export_package_clicked(self) -> None:
        if self._db_path is None or self._project_dir is None:
            QMessageBox.warning(self, "还没有翻译内容", "请先拖入工程并跑一遍翻译。")
            return

        default_name = self._project_dir.name
        game_name, ok = QInputDialog.getText(self, "导出翻译包", "游戏名称：", text=default_name)
        if not ok or not game_name.strip():
            return

        dest_dir = QFileDialog.getExistingDirectory(
            self, "选择翻译包保存位置", str(self._project_dir.parent)
        )
        if not dest_dir:
            return

        try:
            package_path = export_translation_package(
                self._db_path, game_name.strip(), Path(dest_dir)
            )
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        self._log_message(f"翻译包已导出：{package_path}")
        QMessageBox.information(self, "导出完成", f"已生成：{package_path}\n\n可以直接分享给拿到同一个游戏的人。")

    def _on_import_package_clicked(self) -> None:
        if self._project_dir is None:
            QMessageBox.warning(self, "还没有选择工程", "请先把游戏文件夹拖进来，再导入翻译包。")
            return

        package_file, _ = QFileDialog.getOpenFileName(
            self, "选择翻译包", str(self._project_dir.parent), "翻译包 (*.rpgtrans.json)"
        )
        if not package_file:
            return

        if self._db_path is None:
            self._db_path = db_path_for_project(self._project_dir)
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # 先确保本地也跑过一遍 extract——同一版本游戏两边算出来的 TextUnit id 才对得上，
            # 已经翻译过的条目不受影响（upsert_units 原文没变就不覆盖翻译进度）。
            run_extract(self._project_dir, self._db_path)
            imported, skipped = import_translation_package(self._db_path, Path(package_file))
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))
            return

        self._log_message(f"翻译包导入完成：成功 {imported} 条，跳过（版本不匹配）{skipped} 条。")
        if imported > 0:
            self._inject_button.setEnabled(True)
        QMessageBox.information(self, "导入完成", f"成功导入 {imported} 条，跳过 {skipped} 条。")

    def _on_failed(self, message: str) -> None:
        self._log_message(f"出错：{message}")
        QMessageBox.critical(self, "出错", message)
        self._reset_after_translate()

    def _reset_after_translate(self) -> None:
        self._start_button.setEnabled(self._adapter is not None)
        self._stop_button.setVisible(False)
        self._retry_failed_button.setVisible(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)

    def _on_open_output_clicked(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_dir))
