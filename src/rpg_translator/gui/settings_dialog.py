from __future__ import annotations

import httpx
from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rpg_translator.config import (
    Settings,
    clear_deepseek_api_key,
    clear_fallback_api_key,
    clear_local_api_key,
    get_deepseek_api_key,
    get_fallback_api_key,
    get_local_api_key,
    set_deepseek_api_key,
    set_fallback_api_key,
    set_local_api_key,
)
from rpg_translator.translate.batch_translator import DEFAULT_BATCH_SIZE

ORG_NAME = "rpg_translator"
APP_NAME = "rpg_translator"
_MODELS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    # 百炼（DashScope，https://dashscope.aliyuncs.com/compatible-mode/v1）——实测均
    # 连通，qwen3-32b 这类混合思考模型默认开思考模式，非流式调用必须显式传
    # enable_thinking=false 否则 400，llm_client.py 已经对所有请求强制带这个参数。
    "qwen3.7-plus",
    "qwen-plus-2025-07-28",
    "qwen-max",
    "qwen-mt-flash",
    "qwen3.6-plus",
    "qwen3-32b",
    "qwen3.5-35b-a3b",
    # 硅基流动（SiliconFlow，https://api.siliconflow.cn/v1）——实测均连通。
    "deepseek-ai/DeepSeek-V3.2",
    "Qwen/Qwen3.5-35B-A3B",
    "Qwen/Qwen3.5-27B",
]

# "online"：云端 OpenAI 兼容 provider（DeepSeek 等），走通用 prompt，跟以前行为一致。
# "local"：本地跑的小模型（比如 Ollama 部署的 SakuraLLM/GalTransl），走专门适配过的
# prompt 模板和控制码处理（见 translate/sakura_prompt.py），两者不能共用同一套 prompt——
# 直接把 DeepSeek 那套自由格式怼给本地小模型，效果会打折扣（见适配测试记录）。
ENGINE_ONLINE = "online"
ENGINE_LOCAL = "local"


class _ConnectivityCheckWorker(QThread):
    """连接测试的 HTTP 请求跑在这个后台线程——原来直接在 _on_accept 里同步调用
    httpx，网络慢或者地址填错时会把整个（模态）设置对话框卡住最多
    _CONNECTIVITY_TIMEOUT_SECONDS 秒，和这个项目其它地方"耗时操作不占 GUI 线程"
    的一贯做法不一致，也容易被用户误以为软件又双叒卡死了。"""

    finished_check = Signal(bool, str)

    def __init__(
        self,
        url: str,
        base_url: str,
        api_key: str,
        timeout: float,
        transport: httpx.BaseTransport | None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._url = url
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def run(self) -> None:
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as http:
                resp = http.get(self._url, headers={"Authorization": f"Bearer {self._api_key}"})
        except httpx.HTTPError as e:
            self.finished_check.emit(False, f"连不上 {self._base_url}：{e}")
            return
        except Exception as e:  # noqa: BLE001 - 必须兜底，否则线程静默死掉、_busy 永远 True
            # 只 catch httpx.HTTPError catch 不住这类：比如 API Key/Base URL 里混进了
            # 全角字符（中文输入法误触很常见），拼 Authorization header 时 httpx 在
            # 请求还没真正发出前就因为头部非 ASCII 抛 UnicodeEncodeError，不是
            # httpx.HTTPError 的子类。不兜底的话这个 QThread 直接跑死，finished_check
            # 永远不发，_on_accept 里设的 self._busy 永远是 True——对话框的 OK 按钮
            # 保持禁用、reject()（取消/Esc/关闭）也被 _busy 挡住，整个应用的忙碌光标
            # 也恢复不了，用户只能强杀进程。
            self.finished_check.emit(False, f"连接测试出错：{e}")
            return

        # >=500 不当作"连通"：本机走系统代理（比如 Clash）时，代理本身能正常应答，
        # 但代理连不上局域网里的目标地址（比如 Ollama 用了错的端口/IP）会回一个
        # 502/504——这种情况客户端收到的确实是一个完整的 HTTP 响应，但真正要连的
        # 那个地址其实没通，不能算检查通过。4xx（比如 401 key 错）说明请求确实到了
        # 目标服务，只是 key/参数不对，这种算连通，交给真正翻译时的报错反馈。
        if resp.status_code >= 500:
            self.finished_check.emit(
                False,
                f"{self._base_url} 返回了错误状态码 {resp.status_code}，"
                "目标服务可能没启动或地址不对。",
            )
            return
        self.finished_check.emit(True, "")


class SettingsDialog(QDialog):
    """API Key（keyring）、模型选择、并发数、输出目录——非敏感项走 QSettings，
    API Key 单独走 keyring，绝不落地明文文件。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self._qsettings = QSettings(ORG_NAME, APP_NAME)
        # 测试用注入点：换成 httpx.MockTransport 就能在不碰真实网络的情况下验证
        # _ConnectivityCheckWorker 的请求/判断逻辑（同 llm_client.LLMClient 的 transports
        # 参数）。留空（默认）就是走真实网络。
        self._connectivity_transport: httpx.BaseTransport | None = None
        # 连接测试跑在后台线程期间为 True——用来在 reject()/关闭按钮里挡住"检测还没
        # 回来就把对话框关掉"这条路径（销毁一个仍在运行的 QThread 会直接 native abort）。
        self._busy = False
        self._check_worker: _ConnectivityCheckWorker | None = None

        self._engine_combo = QComboBox()
        self._engine_combo.addItem("在线（云端 API，如 DeepSeek）", ENGINE_ONLINE)
        self._engine_combo.addItem("本地模型（如 Ollama 部署的 Sakura）", ENGINE_LOCAL)
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("未设置")

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText(Settings().deepseek_base_url)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)  # 允许填自定义模型名（比如接第三方兼容服务）
        self._model_combo.addItems(_MODELS)
        # _MODELS 现在有十来个条目（多个 provider 的模型都塞在同一个下拉里），不限制
        # 弹出高度的话在小屏幕上会把下拉框撑到超出窗口/屏幕；封顶显示条目数，超出的
        # 部分交给下拉框自带的滚动条。
        self._model_combo.setMaxVisibleItems(8)

        online_form = QFormLayout()
        online_form.addRow("DeepSeek API Key", self._api_key_edit)
        online_form.addRow("Base URL", self._base_url_edit)
        online_form.addRow("模型", self._model_combo)
        self._online_box = QGroupBox("在线 Provider")
        self._online_box.setLayout(online_form)

        # 本地模型走 OpenAI 兼容协议（Ollama 的 /v1 端点就是），API Key 大多数本地
        # 服务不校验，留空会自动填一个占位值，不强制用户填。
        self._local_base_url_edit = QLineEdit()
        self._local_base_url_edit.setPlaceholderText("例如 http://127.0.0.1:11434/v1")
        self._local_model_edit = QLineEdit()
        self._local_model_edit.setPlaceholderText("例如 sakura-galtransl")
        self._local_api_key_edit = QLineEdit()
        self._local_api_key_edit.setPlaceholderText("一般留空即可，本地服务通常不校验")

        local_form = QFormLayout()
        local_form.addRow("Base URL", self._local_base_url_edit)
        local_form.addRow("模型名", self._local_model_edit)
        local_form.addRow("API Key（可选）", self._local_api_key_edit)
        self._local_box = QGroupBox("本地 Provider（走 Sakura 专用 prompt 适配）")
        self._local_box.setLayout(local_form)

        self._concurrency_spin = QSpinBox()
        self._concurrency_spin.setRange(1, 32)

        self._batch_size_spin = QSpinBox()
        self._batch_size_spin.setRange(1, 200)
        self._batch_size_spin.setToolTip(
            "一次请求打包翻译多少条不同文本。调大能减少请求总数、降低撞上服务商限流的"
            "概率，但太大可能让模型回复格式出错（出错会自动退化成逐条重试，不会丢译文，"
            "只是变慢）。"
        )

        form = QFormLayout()
        form.addRow("翻译引擎", self._engine_combo)
        form.addRow("并发数", self._concurrency_spin)
        form.addRow("批量大小", self._batch_size_spin)

        # 备用 provider：主 provider 连续报瞬时错误（429/5xx/连接失败）重试用尽后自动切过来
        # （见 translate/llm_client.py）。三个字段都留空就是不启用，行为和以前一样。
        self._fallback_api_key_edit = QLineEdit()
        self._fallback_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._fallback_api_key_edit.setPlaceholderText("未设置（可留空，不启用故障转移）")
        self._fallback_base_url_edit = QLineEdit()
        self._fallback_base_url_edit.setPlaceholderText("例如 https://api.siliconflow.cn/v1")
        self._fallback_model_edit = QLineEdit()
        self._fallback_model_edit.setPlaceholderText("例如 deepseek-ai/DeepSeek-V4-Flash")

        fallback_form = QFormLayout()
        fallback_form.addRow("备用 API Key", self._fallback_api_key_edit)
        fallback_form.addRow("备用 Base URL", self._fallback_base_url_edit)
        fallback_form.addRow("备用模型", self._fallback_model_edit)
        self._fallback_box = QGroupBox("备用 Provider（可选，主服务连续出错时自动切换，仅在线引擎可用）")
        self._fallback_box.setLayout(fallback_form)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._online_box)
        layout.addWidget(self._local_box)
        layout.addWidget(self._fallback_box)
        layout.addWidget(self._button_box)

        self._load()
        self._on_engine_changed()

    def _on_engine_changed(self) -> None:
        is_local = self._engine_combo.currentData() == ENGINE_LOCAL
        self._local_box.setVisible(is_local)
        self._online_box.setVisible(not is_local)
        self._fallback_box.setVisible(not is_local)

    def _load(self) -> None:
        existing_key = get_deepseek_api_key()
        self._had_api_key = bool(existing_key)
        if existing_key:
            self._api_key_edit.setText(existing_key)

        self._base_url_edit.setText(str(self._qsettings.value("base_url", "")))

        model = self._qsettings.value("model", _MODELS[0])
        index = self._model_combo.findText(model)
        if index >= 0:
            self._model_combo.setCurrentIndex(index)
        else:
            self._model_combo.setCurrentText(model)

        concurrency = int(self._qsettings.value("concurrency", 4))
        self._concurrency_spin.setValue(concurrency)

        batch_size = int(self._qsettings.value("batch_size", DEFAULT_BATCH_SIZE))
        self._batch_size_spin.setValue(batch_size)

        engine = str(self._qsettings.value("engine", ENGINE_ONLINE))
        index = self._engine_combo.findData(engine)
        self._engine_combo.setCurrentIndex(index if index >= 0 else 0)

        self._local_base_url_edit.setText(str(self._qsettings.value("local_base_url", "")))
        self._local_model_edit.setText(str(self._qsettings.value("local_model", "")))
        existing_local_key = get_local_api_key()
        self._had_local_key = bool(existing_local_key)
        if existing_local_key:
            self._local_api_key_edit.setText(existing_local_key)

        existing_fallback_key = get_fallback_api_key()
        self._had_fallback_key = bool(existing_fallback_key)
        if existing_fallback_key:
            self._fallback_api_key_edit.setText(existing_fallback_key)
        self._fallback_base_url_edit.setText(str(self._qsettings.value("fallback_base_url", "")))
        self._fallback_model_edit.setText(str(self._qsettings.value("fallback_model", "")))

    # /models 是 OpenAI 兼容协议里最轻量的探活端点：不花 token、不需要 key 一定正确
    # 就能拿到响应（key 不对也会收到 401，那也说明服务本身是通的）——只用来确认"连得上
    # 这个地址"，不判断 key/模型名是否真的可用，那些错误留给真正翻译时的报错反馈。
    _CONNECTIVITY_TIMEOUT_SECONDS = 8.0

    def _resolve_check_target(self) -> tuple[str, str] | None:
        """只做字段本身的轻量校验（是否为空），不碰网络——这部分留在 GUI 线程同步做
        没问题。返回 None 表示校验没过，对应的错误提示已经弹出，调用方直接放弃
        这次保存，不需要再起后台线程。"""
        engine = self._engine_combo.currentData()
        if engine == ENGINE_LOCAL:
            base_url = self._local_base_url_edit.text().strip()
            if not base_url:
                self._show_connectivity_error("本地 Provider 的 Base URL 不能为空。")
                return None
            api_key = self._local_api_key_edit.text().strip() or "sk-local"
        else:
            base_url = self._base_url_edit.text().strip() or Settings().deepseek_base_url
            api_key = self._api_key_edit.text().strip()
            if not api_key:
                self._show_connectivity_error("在线 Provider 需要填写 API Key。")
                return None
        return base_url, api_key

    def _show_connectivity_error(self, error: str) -> None:
        QMessageBox.warning(
            self,
            "连接测试失败",
            f"{error}\n\n设置未保存，请检查地址/网络，或确认服务已启动后重试。",
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._button_box.setEnabled(not busy)
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def reject(self) -> None:
        # 连接测试的后台线程还没跑完时不放行关闭——Cancel 按钮、标题栏叉号、Esc
        # 默认都会走到这个方法。对话框在检测线程还活着的时候被销毁，等同于销毁一个
        # 仍在运行的 QThread，PySide 里这会在 C++ 层直接 abort（这个项目已经踩过
        # 好几次这类无预兆闪退，见 main_window.py _open_settings 的说明）。检测本身
        # 有 _CONNECTIVITY_TIMEOUT_SECONDS 封顶，等一下就会回来。
        if self._busy:
            return
        super().reject()

    def _on_accept(self) -> None:
        target = self._resolve_check_target()
        if target is None:
            return
        base_url, api_key = target

        url = base_url.rstrip("/") + "/models"
        self._set_busy(True)
        self._check_worker = _ConnectivityCheckWorker(
            url, base_url, api_key, self._CONNECTIVITY_TIMEOUT_SECONDS, self._connectivity_transport, self
        )
        self._check_worker.finished_check.connect(self._on_connectivity_checked)
        self._check_worker.start()

    def _on_connectivity_checked(self, ok: bool, error: str) -> None:
        self._set_busy(False)
        if not ok:
            self._show_connectivity_error(error)
            return
        self._save_settings()
        self.accept()

    def _save_settings(self) -> None:
        api_key = self._api_key_edit.text().strip()
        if api_key:
            set_deepseek_api_key(api_key)
        elif self._had_api_key:
            clear_deepseek_api_key()

        fallback_key = self._fallback_api_key_edit.text().strip()
        if fallback_key:
            set_fallback_api_key(fallback_key)
        elif self._had_fallback_key:
            clear_fallback_api_key()

        local_key = self._local_api_key_edit.text().strip()
        if local_key:
            set_local_api_key(local_key)
        elif self._had_local_key:
            clear_local_api_key()

        self._qsettings.setValue("base_url", self._base_url_edit.text().strip())
        self._qsettings.setValue("model", self._model_combo.currentText())
        self._qsettings.setValue("concurrency", self._concurrency_spin.value())
        self._qsettings.setValue("batch_size", self._batch_size_spin.value())
        self._qsettings.setValue("engine", self._engine_combo.currentData())
        self._qsettings.setValue("local_base_url", self._local_base_url_edit.text().strip())
        self._qsettings.setValue("local_model", self._local_model_edit.text().strip())
        self._qsettings.setValue("fallback_base_url", self._fallback_base_url_edit.text().strip())
        self._qsettings.setValue("fallback_model", self._fallback_model_edit.text().strip())

    @property
    def model(self) -> str:
        return str(self._qsettings.value("model", _MODELS[0]))

    @property
    def base_url(self) -> str:
        return str(self._qsettings.value("base_url", "")) or Settings().deepseek_base_url

    @property
    def concurrency(self) -> int:
        return int(self._qsettings.value("concurrency", 4))

    @property
    def batch_size(self) -> int:
        return int(self._qsettings.value("batch_size", DEFAULT_BATCH_SIZE))

    @property
    def engine(self) -> str:
        return str(self._qsettings.value("engine", ENGINE_ONLINE))

    @property
    def local_base_url(self) -> str:
        return str(self._qsettings.value("local_base_url", ""))

    @property
    def local_model(self) -> str:
        return str(self._qsettings.value("local_model", ""))

    @property
    def fallback_base_url(self) -> str | None:
        return str(self._qsettings.value("fallback_base_url", "")) or Settings().fallback_base_url

    @property
    def fallback_model(self) -> str | None:
        return str(self._qsettings.value("fallback_model", "")) or Settings().fallback_model


def resolve_base_url(qsettings: QSettings) -> str:
    """GUI 设置里填了 Base URL 就用它，留空则退回 .env 里的默认值。main_window.py 和
    SettingsDialog 都要用同一份解析逻辑，不然容易出现"设置里存了但实际没生效"的错位。"""
    return str(qsettings.value("base_url", "")) or Settings().deepseek_base_url


def resolve_fallback_config(qsettings: QSettings) -> tuple[str | None, str | None, str | None]:
    """同上，用于备用 provider 的三个字段：(api_key, base_url, model)。"""
    api_key = get_fallback_api_key()
    base_url = str(qsettings.value("fallback_base_url", "")) or Settings().fallback_base_url
    model = str(qsettings.value("fallback_model", "")) or Settings().fallback_model
    return api_key, base_url, model


def resolve_local_config(qsettings: QSettings) -> tuple[str, str, str]:
    """本地 provider 的三个字段：(api_key, base_url, model)。api_key 大多数本地服务
    不校验，留空时填一个占位值而不是空字符串——LLMClient 要求 Authorization header
    非空，本地服务实际上不检查这个值。"""
    api_key = get_local_api_key() or "sk-local"
    base_url = str(qsettings.value("local_base_url", ""))
    model = str(qsettings.value("local_model", ""))
    return api_key, base_url, model
