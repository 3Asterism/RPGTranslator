from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rpg_translator.config import (
    Settings,
    get_deepseek_api_key,
    get_fallback_api_key,
    set_deepseek_api_key,
    set_fallback_api_key,
)

ORG_NAME = "rpg_translator"
APP_NAME = "rpg_translator"
_MODELS = ["deepseek-v4-flash", "deepseek-v4-pro"]


class SettingsDialog(QDialog):
    """API Key（keyring）、模型选择、并发数、输出目录——非敏感项走 QSettings，
    API Key 单独走 keyring，绝不落地明文文件。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self._qsettings = QSettings(ORG_NAME, APP_NAME)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("未设置")

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText(Settings().deepseek_base_url)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)  # 允许填自定义模型名（比如接第三方兼容服务）
        self._model_combo.addItems(_MODELS)

        self._concurrency_spin = QSpinBox()
        self._concurrency_spin.setRange(1, 32)

        self._output_dir_edit = QLineEdit()
        browse_button = QPushButton("浏览…")
        browse_button.clicked.connect(self._browse_output_dir)
        output_dir_layout = QHBoxLayout()
        output_dir_layout.addWidget(self._output_dir_edit)
        output_dir_layout.addWidget(browse_button)

        form = QFormLayout()
        form.addRow("DeepSeek API Key", self._api_key_edit)
        form.addRow("Base URL", self._base_url_edit)
        form.addRow("模型", self._model_combo)
        form.addRow("并发数", self._concurrency_spin)
        form.addRow("输出目录", output_dir_layout)

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
        fallback_box = QGroupBox("备用 Provider（可选，主服务连续出错时自动切换）")
        fallback_box.setLayout(fallback_form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(fallback_box)
        layout.addWidget(buttons)

        self._load()

    def _browse_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self._output_dir_edit.text())
        if directory:
            self._output_dir_edit.setText(directory)

    def _load(self) -> None:
        existing_key = get_deepseek_api_key()
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

        self._output_dir_edit.setText(str(self._qsettings.value("output_dir", "output")))

        existing_fallback_key = get_fallback_api_key()
        if existing_fallback_key:
            self._fallback_api_key_edit.setText(existing_fallback_key)
        self._fallback_base_url_edit.setText(str(self._qsettings.value("fallback_base_url", "")))
        self._fallback_model_edit.setText(str(self._qsettings.value("fallback_model", "")))

    def _on_accept(self) -> None:
        api_key = self._api_key_edit.text().strip()
        if api_key:
            set_deepseek_api_key(api_key)

        fallback_key = self._fallback_api_key_edit.text().strip()
        if fallback_key:
            set_fallback_api_key(fallback_key)

        self._qsettings.setValue("base_url", self._base_url_edit.text().strip())
        self._qsettings.setValue("model", self._model_combo.currentText())
        self._qsettings.setValue("concurrency", self._concurrency_spin.value())
        self._qsettings.setValue("output_dir", self._output_dir_edit.text())
        self._qsettings.setValue("fallback_base_url", self._fallback_base_url_edit.text().strip())
        self._qsettings.setValue("fallback_model", self._fallback_model_edit.text().strip())
        self.accept()

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
    def output_dir(self) -> str:
        return str(self._qsettings.value("output_dir", "output"))

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
