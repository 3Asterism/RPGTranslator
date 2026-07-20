"""调试脚本：用 pywinauto 驱动**真正打包出来的 exe**、发**真实鼠标点击/键盘输入**
（不是 Python 直接调用 window._on_start_clicked() 那种），复现"翻译一点 -> 停止 ->
设置里切换 -> 继续翻译"闪退。

背景：之前所有复现尝试（含真实工程 + 真实窗口平台 + 真实 API + 反复轮次）都是靠
Python 直接调用 MainWindow 的槽函数，从没有真正模拟过鼠标点击/键盘输入这条路径——
真机是用户手动点鼠标复现的，两者理论上应该等价（槽函数本身就是点击触发的那个），
但这是目前唯一还没排除的差异变量，动用 UI 自动化把这个变量也堵上。

为了绕开"拖拽游戏文件夹到窗口"这个没法脚本化的动作，gui/app.py 新增了一个命令行
参数：传工程目录直接跳过拖拽加载（见 app.py 的改动说明），只影响自动化测试，双击
正常启动不受影响。

为控制成本，每轮只等几秒钟就点停止，不会真的把 2 万多条 pending 翻完。

直接跑：.venv/Scripts/python.exe tests/test_pywinauto_real_click_repro.py
（会真的弹出一个可见窗口、真的点鼠标、真的打线上 API，属于预期行为）
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXE_PATH = REPO_ROOT / "dist_autotest" / "RPGTranslator" / "RPGTranslator.exe"
REAL_PROJECT_DIR = Path(r"D:\project\new\rpgTranslator\game\PrincessProject")
LOG_PATH = REPO_ROOT / "dist_autotest" / "RPGTranslator" / "logs" / "app.log"

from pywinauto import Application  # noqa: E402
from pywinauto.timings import wait_until_passes  # noqa: E402


def _tail_log(n: int = 20) -> str:
    if not LOG_PATH.is_file():
        return "(日志文件还不存在)"
    text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def main() -> int:
    assert EXE_PATH.is_file(), f"找不到打包好的 exe：{EXE_PATH}（先跑 scripts/build.py 或类似命令产出到 dist_autotest）"
    assert REAL_PROJECT_DIR.is_dir(), f"真实工程目录不存在：{REAL_PROJECT_DIR}"

    if LOG_PATH.is_file():
        LOG_PATH.unlink()  # 清掉旧日志，这次复现的内容从头开始，方便看

    print(f"[repro] 启动真实 exe：{EXE_PATH} \"{REAL_PROJECT_DIR}\"", flush=True)
    app = Application(backend="uia").start(f'"{EXE_PATH}" "{REAL_PROJECT_DIR}"')
    proc_pid = app.process

    try:
        main_win = wait_until_passes(
            30, 0.5, lambda: app.window(title_re=".*RPG Maker.*")
        )
        main_win.wait("visible", timeout=30)
        print("[repro] 主窗口已出现", flush=True)

        start_button = main_win.child_window(title="开始翻译", control_type="Button")
        start_button.wait("enabled", timeout=30)
        print("[repro] 「开始翻译」按钮已可点（说明真实工程已经识别成功）", flush=True)

        for round_idx in range(1, 4):
            print(f"\n[repro] ===== 第 {round_idx} 轮：真实点击「开始翻译」 =====", flush=True)
            start_button.click_input()

            time.sleep(4.0)  # 真实渲染 + 真实网络请求跑一会儿
            if not _process_alive(proc_pid):
                print("[repro] !!! 进程已经不在了（点开始翻译之后就没了）", flush=True)
                print(_tail_log(40), flush=True)
                return 1

            print(f"[repro] 第 {round_idx} 轮：真实点击「停止」", flush=True)
            stop_button = main_win.child_window(title="停止", control_type="Button")
            if stop_button.exists(timeout=5):
                stop_button.click_input()
            else:
                print("[repro] 「停止」按钮没找到（可能已经翻完了，跳过停止）", flush=True)

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if not _process_alive(proc_pid):
                    print("[repro] !!! 进程在等停止期间消失了", flush=True)
                    print(_tail_log(40), flush=True)
                    return 1
                if start_button.is_enabled():
                    break
                time.sleep(0.3)
            print(f"[repro] 第 {round_idx} 轮：已停止（或翻完），start 按钮重新可点", flush=True)

            print(f"[repro] 第 {round_idx} 轮：真实点击设置按钮，切一下并发数并保存", flush=True)
            settings_button = main_win.child_window(title="⚙ 设置", control_type="Button")
            settings_button.click_input()

            # deleteLater() 是异步收尾，上一轮的 SettingsDialog 这时候可能还没被
            # 真正销毁（只是隐藏），UIA 树里会同时看到旧的（隐藏）和新的（可见）
            # 两个 SettingsDialog——按 is_visible() 过滤，只挑当前真正显示的那个。
            candidates = [
                c for c in main_win.descendants(control_type="Window")
                if c.element_info.automation_id == "QApplication.SettingsDialog"
            ]
            visible = [c for c in candidates if c.is_visible()]
            assert len(visible) == 1, f"预期正好 1 个可见的设置对话框，实际 {len(visible)} 个"
            dialog = visible[0]
            spinners = [
                c for c in dialog.descendants(control_type="Spinner")
                if c.element_info.automation_id == "QApplication.SettingsDialog.QSpinBox"
            ]
            concurrency_spin = spinners[0]
            # 真实键盘输入：全选现有内容再打几个数字进去，走真实 IME/键盘事件管线，
            # 不是 Python 直接 setValue()。
            concurrency_spin.click_input()
            concurrency_spin.type_keys("^a", pause=0.05)
            concurrency_spin.type_keys("6", pause=0.05)

            ok_buttons = [c for c in dialog.descendants(control_type="Button") if c.window_text() == "OK"]
            ok_buttons[0].click_input()

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not _process_alive(proc_pid):
                    print("[repro] !!! 进程在设置保存期间消失了", flush=True)
                    print(_tail_log(40), flush=True)
                    return 1
                try:
                    if not dialog.is_visible():
                        break
                except Exception:
                    break
                time.sleep(0.3)
            print(f"[repro] 第 {round_idx} 轮：设置对话框已关闭", flush=True)

        print("\nREPRO_SCRIPT_COMPLETED_OK（3 轮下来进程还活着）", flush=True)
        return 0
    finally:
        if _process_alive(proc_pid):
            try:
                app.kill()
            except Exception:
                pass


def _process_alive(pid: int) -> bool:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ 'alive' }} else {{ 'dead' }}"],
        capture_output=True, text=True,
    )
    return "alive" in result.stdout


if __name__ == "__main__":
    sys.exit(main())
