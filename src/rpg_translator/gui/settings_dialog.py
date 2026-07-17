from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rpg_translator.config import get_deepseek_api_key, set_deepseek_api_key

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

        self._model_combo = QComboBox()
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
        form.addRow("模型", self._model_combo)
        form.addRow("并发数", self._concurrency_spin)
        form.addRow("输出目录", output_dir_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
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

        model = self._qsettings.value("model", _MODELS[0])
        index = self._model_combo.findText(model)
        self._model_combo.setCurrentIndex(index if index >= 0 else 0)

        concurrency = int(self._qsettings.value("concurrency", 4))
        self._concurrency_spin.setValue(concurrency)

        self._output_dir_edit.setText(str(self._qsettings.value("output_dir", "output")))

    def _on_accept(self) -> None:
        api_key = self._api_key_edit.text().strip()
        if api_key:
            set_deepseek_api_key(api_key)

        self._qsettings.setValue("model", self._model_combo.currentText())
        self._qsettings.setValue("concurrency", self._concurrency_spin.value())
        self._qsettings.setValue("output_dir", self._output_dir_edit.text())
        self.accept()

    @property
    def model(self) -> str:
        return str(self._qsettings.value("model", _MODELS[0]))

    @property
    def concurrency(self) -> int:
        return int(self._qsettings.value("concurrency", 4))

    @property
    def output_dir(self) -> str:
        return str(self._qsettings.value("output_dir", "output"))
