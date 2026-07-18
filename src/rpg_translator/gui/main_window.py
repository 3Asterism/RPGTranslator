from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl, Signal
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
/* 不自定义 QComboBox::drop-down/::down-arrow——QSS 一旦碰了 ::drop-down，Qt 就不
   再画原生箭头，之前这里定义了 ::drop-down 却没配套画箭头，导致所有下拉框（包括
   原来的"模型"选择框）看起来都不像能点开。试过用 QSS 边框三角形技巧补一个箭头，
   但 Qt 的 QSS 盒模型不支持透明边框拼三角形（会渲染成一个实心色块），不是真正的
   三角形——与其加图片资源文件，不如干脆不碰这两个子控件，让 Qt 画各平台原生的
   下拉箭头，零资源依赖、保证渲染正确。 */
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


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    total_minutes = round(seconds / 60)
    if total_minutes < 60:
        return f"{total_minutes} 分钟"
    hours, minutes = divmod(total_minutes, 60)
    # "1.3 小时" 得自己心算才知道是多少分钟，直接拆成"小时+分钟"更直观。
    return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"


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

        # log_message/stage_changed 信号来自后台线程，两个 provider 都报错时高并发
        # 下每个批次的每次重试/限流冷却/换 provider 都各发一条（见 llm_client.py 的
        # on_log），跟 _on_progress_changed 注释里那次"appendPlainText 撑爆崩溃"是
        # 同一类问题——那次修的是 progress_changed 的节流，这里 log_message 从来没
        # 节流过。缓冲 + 定时器批量落盘，把"来一条就跨线程 append 一次控件"降成
        # 固定频率的批量刷新，不管后台产生消息多快，GUI 线程这边的更新频率封顶。
        self._pending_log_lines: list[str] = []
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(150)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)
        self._log_flush_timer.start()

        # 本次会话（软件跑起来到现在）累计的 token 用量/预估花费——重开软件清零，
        # 不落地存储，纯粹是给正在跑的这一轮汉化一个实时的量级参考。
        self._session_prompt_tokens = 0
        self._session_completion_tokens = 0
        self._session_cost_cny = 0.0
        self._session_has_unpriced_model = False

        # 翻译速度/剩余时间预估：只在当前这一次 TranslateWorker 运行期间有意义，每次
        # 重新起 worker（首次翻译、重试失败项）都要清空，不能带着上一轮的速度残留。
        # 用最近一小段窗口内的样本算速度（而不是从头到尾的总平均），是因为翻译请求是
        # 一批一批完成的（见 batch_translator.py），进度是一阵一阵跳的，用最近窗口能
        # 更快反映"最近是不是变快/变慢了"（比如撞到限流退避），比全程平均更贴近实时。
        self._progress_samples: deque[tuple[float, int]] = deque(maxlen=20)
        # 见 _on_progress_changed 的节流注释；-1 保证 worker 起来后第一条进度必然打印。
        self._last_progress_log_time: float = -1.0
        # 速度/ETA 文本单独节流：显示这一步按更长的间隔重算一次，避免跟着每次进度
        # 信号刷新（人眼跟不上）。
        self._last_eta_update_time: float = -1.0
        # 样本本身也要按真实时间间隔采集，不能每次进度信号来了就存一个——现在翻译是
        # 按事件页面分组打包提交的（见 batch_translator.py），一次请求解析完，几十条
        # 台词的 on_progress 会在同一个同步循环里背靠背连续触发，这些样本时间戳几乎
        # 相同。如果这几十个样本恰好把 deque(maxlen=20) 的窗口填满，"最老样本到现在"
        # 的 elapsed 会趋近于 0，而 completed 差值却是几十，算出来的速度会离谱地飙到
        # 几千甚至上万批/分钟（实测复现过 11004.4 批/分钟）。按最小时间间隔取样能
        # 保证同一批请求内部的连续触发只记一个点，窗口跨度反映的是真实请求节奏。
        self._last_sample_time: float = -1.0
        self._SAMPLE_MIN_INTERVAL_SECONDS = 1.0

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

        self._eta_label = QLabel("")
        self._eta_label.setObjectName("etaLabel")

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(160)
        # 大工程翻译动辄几万条，每条都往这里 append 一行会让文档 + 撤销历史无限膨胀
        # （QPlainTextEdit 默认开着撤销栈，只读控件用不上撤销），实测这是导致长时间
        # 翻译后原生崩溃（ucrtbase fastfail，Python 异常接不住）的主因之一，见
        # _on_progress_changed 的节流注释。这里做兜底：只读控件关掉撤销，行数封顶。
        self._log.setUndoRedoEnabled(False)
        self._log.setMaximumBlockCount(5000)

        translate_box = QGroupBox("2. 翻译（后台跑，结果先存本地，不动游戏文件）")
        translate_layout = QVBoxLayout(translate_box)
        translate_layout.addLayout(start_row)
        translate_layout.addWidget(self._progress_bar)
        translate_layout.addWidget(self._eta_label)
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
        # 只做轻量的缓冲入列 + 落文件日志，真正touch控件的 appendPlainText 挪到
        # _flush_log_buffer 里按固定频率批量执行（见 __init__ 里 _log_flush_timer
        # 的说明），不管这个方法本身被跨线程调用得多频繁，都不会直接怼控件。
        self._pending_log_lines.append(message)
        logger.info(message)  # 同步落一份到文件日志，软件本身崩了也能翻记录复盘

    def _flush_log_buffer(self) -> None:
        if not self._pending_log_lines:
            return
        self._log.appendPlainText("\n".join(self._pending_log_lines))
        self._pending_log_lines.clear()

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

        self._ensure_worker_stopped(self._extract_worker)
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
            # 本地量化小模型处理一批几十条的请求天然比云端 API 慢（实测局域网测试机
            # 上一批 20 行在并发负载下就要 15+ 秒），共用云端那套 60 秒超时容易在
            # 批次较大或并发排队时误触发超时重试，反而更慢——给本地引擎一个更宽松
            # 的超时。
            timeout = 180.0
        else:
            model = str(qsettings.value("model", "deepseek-v4-flash"))
            base_url = resolve_base_url(qsettings)
            fallback_api_key, fallback_base_url, fallback_model = resolve_fallback_config(qsettings)
            api_key = get_deepseek_api_key()
            prompt_strategy = DEFAULT_PROMPT_STRATEGY
            timeout = 60.0

        self._retry_failed_button.setVisible(False)
        self._progress_samples.clear()
        self._last_progress_log_time = -1.0
        self._last_eta_update_time = -1.0
        self._last_sample_time = -1.0
        self._eta_label.setText("翻译速度：统计中…")
        self._ensure_worker_stopped(self._translate_worker)
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
            timeout=timeout,
        )
        self._translate_worker.stage_changed.connect(self._log_message)
        self._translate_worker.log_message.connect(self._log_message)
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

        now = time.monotonic()
        # 缓存命中多的时候（翻译记忆库里已经有译文），batch_translator.py 会在一个同步
        # 循环里连续 emit 这个信号，完全不受网络请求节奏限制——不节流的话每一条都
        # appendPlainText 一行，短时间内几千次跨线程信号 + 控件重排版会把内存和 Qt
        # 事件队列撑爆（这就是之前那次翻译到一半原生崩溃、Python 侧却没有任何异常
        # 记录的原因）。进度条仍然每次都更新（开销低），日志文本行、速度采样、ETA
        # 显示各自按自己的时间间隔节流（见下方）。
        if completed >= total or now - self._last_progress_log_time >= 0.5:
            self._last_progress_log_time = now
            self._log_message(f"翻译批次 {completed}/{total}")

        if (
            completed >= total
            or self._last_sample_time < 0
            or now - self._last_sample_time >= self._SAMPLE_MIN_INTERVAL_SECONDS
        ):
            self._last_sample_time = now
            self._progress_samples.append((now, completed))

        if completed >= total or now - self._last_eta_update_time >= 2.0:
            self._last_eta_update_time = now
            self._eta_label.setText(self._compute_eta_text(completed, total, now))

    def _compute_eta_text(self, completed: int, total: int, now: float) -> str:
        """用最近窗口（见 self._progress_samples）里最老一个样本到现在的速度估算剩余
        时间——只是个粗略参考：真实速度会随并发占用、provider 限流退避、批次内条目数
        多少而波动，不是恒定值。"""
        if completed <= 0 or len(self._progress_samples) < 2:
            return "翻译速度：统计中…"

        oldest_time, oldest_completed = self._progress_samples[0]
        elapsed = now - oldest_time
        done_in_window = completed - oldest_completed
        if elapsed <= 0 or done_in_window <= 0:
            return "翻译速度：统计中…"

        rate_per_second = done_in_window / elapsed
        speed_text = f"约 {rate_per_second * 60:.1f} 批/分钟"

        remaining = max(total - completed, 0)
        if remaining == 0:
            return f"翻译速度：{speed_text}"

        eta_seconds = remaining / rate_per_second
        return (
            f"翻译速度：{speed_text} · 预计剩余 {_format_duration(eta_seconds)}"
            f"（还剩 {remaining} 批）"
        )

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
        self._eta_label.setText("")
        self._start_button.setEnabled(True)
        self._inject_button.setEnabled(True)
        self._stop_button.setVisible(False)
        self._retry_failed_button.setVisible(bool(failures))

    def _on_retry_failed_clicked(self) -> None:
        """失败条目还保留着 status="pending"，直接重跑翻译这一步就够——不用再走一遍
        提取（工程文本也没变）。"""
        self._log_message("重试失败项…")
        self._start_translate_worker()

    def _ensure_worker_stopped(self, worker: QThread | None) -> None:
        """在把 self._xxx_worker 指向一个新线程、丢掉旧引用之前，确保旧线程真的已经
        跑完。正常按钮状态下旧线程在这里必然已经结束（isRunning() 为 False，wait()
        立即返回），这里只是加一道保险：一旦这个前提在某次时序下不成立，旧 QThread
        对象在引用被覆盖的瞬间因为 Python 侧引用计数归零而被销毁——PySide 里销毁一个
        仍在运行的 QThread 会在 C++ 层直接 qFatal/abort，表现为整个程序无预兆闪退，
        既不会弹「出错」对话框，也不会被 logging_setup.py 的 sys.excepthook 记录
        （那只能捕获 Python 异常，接不住 native abort）。"""
        if worker is not None and worker.isRunning():
            worker.wait(5000)

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

        self._ensure_worker_stopped(self._inject_worker)
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
        self._eta_label.setText("")

    def _on_open_output_clicked(self) -> None:
        if self._output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._output_dir))
