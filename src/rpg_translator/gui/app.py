from __future__ import annotations

import sys
from pathlib import Path

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
    # 命令行传工程目录时直接加载，跳过拖拽——排查"静默闪退"用 UI 自动化（pywinauto
    # 之类）驱动真实打包 exe 复现时，拖拽这个动作本身没法脚本化，这里给自动化留个
    # 口子；不传参数（正常双击启动）行为不变。
    if len(sys.argv) > 1:
        project_dir = Path(sys.argv[1])
        if project_dir.is_dir():
            window._on_path_dropped(project_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
