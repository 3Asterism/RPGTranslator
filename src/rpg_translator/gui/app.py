from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from rpg_translator.gui.main_window import APP_STYLESHEET, MainWindow
from rpg_translator.logging_setup import setup_logging


def main() -> int:
    setup_logging()  # 必须在 QApplication 之前：越早捕获越早的崩溃越好
    app = QApplication(sys.argv)
    # 应用级样式表：QComboBox 下拉列表、QMessageBox 等顶层弹出窗口只有这样才能盖到，
    # 单独 setStyleSheet 在某个 widget 上级联不到这些独立的顶层弹窗（见 main_window.py
    # APP_STYLESHEET 的说明）。
    app.setStyleSheet(APP_STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
