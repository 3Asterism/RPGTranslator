"""打包 GUI 为 Windows 可执行文件（--onedir 模式，见 spec 第 12 节）。

用法：.venv\\Scripts\\python.exe scripts\\build.py
产出：dist\\RPGTranslator\\RPGTranslator.exe
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--noconsole",
        "--noconfirm",
        "--name",
        "RPGTranslator",
        "--paths",
        str(ROOT / "src"),
        str(ROOT / "src" / "rpg_translator" / "gui" / "app.py"),
    ]
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
