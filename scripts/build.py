"""打包 GUI 为 Windows 可执行文件（--onedir 模式，见 spec 第 12 节）。

用法：.venv\\Scripts\\python.exe scripts\\build.py
产出：dist\\RPGTranslator\\RPGTranslator.exe
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_APP_DIR = ROOT / "dist" / "RPGTranslator"

_UNITY_MOD_VARIANTS = ("mono_x86", "mono_x64", "il2cpp_x86", "il2cpp_x64")


def _bundle_unity_mod_assets() -> None:
    """把本地已经跑过 scripts/fetch_unity_mod_assets.py 产出的 resources/unity_mod/
    四个变体目录拷进打包产物——deploy()（unity/deploy.py）运行时从
    get_app_root() / "resources" / "unity_mod" 找这些文件，frozen 情况下
    get_app_root() 就是这个 dist/RPGTranslator/ 目录（见 translate/local_engine.py
    的 get_app_root）。本地没跑过 fetch 脚本时 resources/unity_mod/ 不存在，直接
    跳过——不阻塞常规打包，只是这份产物里 Unity 支持不可用（跟 find_bundled_engine
    对本地引擎缺失时的降级方式一致），不拷贝 _downloads/（下载缓存的原始 zip，
    运行时用不上）和 SOURCES.md（来源记录，不是运行时依赖）。"""
    src_root = ROOT / "resources" / "unity_mod"
    if not src_root.is_dir():
        print("[build] resources/unity_mod/ 不存在，跳过 Unity mod 素材打包"
              "（先跑 scripts/fetch_unity_mod_assets.py 才能让打包产物支持 Unity）")
        return
    dest_root = DIST_APP_DIR / "resources" / "unity_mod"
    for variant in _UNITY_MOD_VARIANTS:
        src = src_root / variant
        if not src.is_dir():
            print(f"[build] resources/unity_mod/{variant} 不存在，跳过")
            continue
        dest = dest_root / variant
        shutil.copytree(src, dest, dirs_exist_ok=True)
        print(f"[build] 已打包 resources/unity_mod/{variant}")


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--noconsole",
        "--noconfirm",
        "--noupx",  # UPX 压缩壳是杀毒软件对 PyInstaller exe 误报的常见诱因之一，关掉降低概率
        "--name",
        "RPGTranslator",
        "--paths",
        str(ROOT / "src"),
        str(ROOT / "src" / "rpg_translator" / "gui" / "app.py"),
    ]
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    _bundle_unity_mod_assets()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
