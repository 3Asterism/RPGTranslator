from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def setup_logging() -> Path:
    """初始化滚动文件日志 + 全局异常兜底，GUI 启动时第一件事就调用。

    PyInstaller `--noconsole` 打包后 sys.stdout/sys.stderr 是 None（模拟 pythonw.exe
    的行为），任何代码不小心往这两个写东西（print、某个依赖库的警告）都会直接
    AttributeError；这个项目之前完全没有落地日志，出问题除了应用内日志面板（软件本身
    已经挂了就看不到）什么都留不下。这里做两件事：1）挂一个滚动文件日志，记录关键阶段
    和所有未捕获异常；2）兜底 stdout/stderr 为 None 的情况，让偶发的 print/警告不会自己
    变成一次新的、更莫名其妙的崩溃。
    """
    log_dir = _base_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)

    if sys.stdout is None:
        sys.stdout = open(log_dir / "console.log", "a", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(log_dir / "console.log", "a", encoding="utf-8")

    sys.excepthook = _make_excepthook(log_file)
    threading.excepthook = _log_thread_exception

    logging.getLogger(__name__).info("日志初始化完成：%s", log_file)
    return log_file


def _make_excepthook(log_file: Path):
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("uncaught").critical(
            "未捕获异常（主线程）", exc_info=(exc_type, exc_value, exc_tb)
        )
        _show_crash_dialog(exc_value, log_file)

    return _hook


def _log_thread_exception(args) -> None:  # noqa: ANN001 - threading.ExceptHookArgs
    logging.getLogger("uncaught.thread").critical(
        "未捕获异常（子线程）", exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )


def _show_crash_dialog(exc_value: BaseException, log_file: Path) -> None:
    """尽力弹一个提示框告知用户"出错了、日志在哪"——弹不出来（比如 QApplication 还
    没建好）就算了，不能让"报告崩溃"这个动作本身再抛一次异常。"""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        if app is None:
            return
        QMessageBox.critical(
            None,
            "出现意外错误",
            f"{exc_value}\n\n详细信息已记录到：\n{log_file}",
        )
    except Exception:
        pass
