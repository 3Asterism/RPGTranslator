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


def _bundle_translation_font() -> None:
    """把本地已经跑过 scripts/fetch_translation_font.py 产出的 resources/fonts/
    拷进打包产物——engines/mv_mz.py 的 patch_font_for_chinese() 运行时从
    get_app_root() / "resources" / "fonts" 找这份字体，frozen 情况下
    get_app_root() 就是这个 dist/RPGTranslator/ 目录。本地没跑过 fetch 脚本时
    resources/fonts/ 不存在，直接跳过——不阻塞常规打包，只是这份产物里 MV/MZ
    注入不会自动修字体（跟 _bundle_unity_mod_assets 的降级方式一致）。"""
    src = ROOT / "resources" / "fonts"
    if not src.is_dir():
        print("[build] resources/fonts/ 不存在，跳过字体打包"
              "（先跑 scripts/fetch_translation_font.py 才能让打包产物支持自动修复缺字）")
        return
    dest = DIST_APP_DIR / "resources" / "fonts"
    shutil.copytree(src, dest, dirs_exist_ok=True)
    print("[build] 已打包 resources/fonts/")


def _bundle_wolf_dec() -> None:
    """把本地已经跑过 scripts/fetch_wolf_dec.py 产出的 resources/wolf_dec/ 拷进
    打包产物——engines/wolf_archive.py 的 find_uberwolf_cli() 运行时从
    get_app_root() / "resources" / "wolf_dec" 找这个 exe，frozen 情况下
    get_app_root() 就是这个 dist/RPGTranslator/ 目录。本地没跑过 fetch 脚本时
    resources/wolf_dec/ 不存在，直接跳过——不阻塞常规打包，只是这份产物里遇到
    "整个 Data 目录打包成单个 Data.wolf" 这种 WOLF RPG Editor 发行版会报错要求
    先解包（跟 _bundle_unity_mod_assets 的降级方式一致），不拷贝 SOURCES.md（来源
    记录，不是运行时依赖）。"""
    src = ROOT / "resources" / "wolf_dec" / "UberWolfCli.exe"
    if not src.is_file():
        print("[build] resources/wolf_dec/UberWolfCli.exe 不存在，跳过"
              "（先跑 scripts/fetch_wolf_dec.py 才能让打包产物支持解包 WOLF Data.wolf）")
        return
    dest_dir = DIST_APP_DIR / "resources" / "wolf_dec"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_dir / "UberWolfCli.exe")
    print("[build] 已打包 resources/wolf_dec/UberWolfCli.exe")


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
    _bundle_translation_font()
    _bundle_wolf_dec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
