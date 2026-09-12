"""下载解包 WOLF RPG Editor 打包发行版 Data.wolf 用的第三方工具 UberWolfCli.exe，
落盘到 resources/wolf_dec/。设计背景/为什么不自己重新实现解密见该目录下的
SOURCES.md 和 engines/wolf_archive.py 的说明。

用法：.venv\\Scripts\\python.exe scripts\\fetch_wolf_dec.py
产出：resources/wolf_dec/UberWolfCli.exe

不进 git（第三方二进制），resources/wolf_dec/ 已加进 .gitignore。跟
fetch_translation_font.py/fetch_unity_mod_assets.py 一样不在自动化测试/CI 里跑
（要联网下载），下载复用 build_full.py 已有的 sha256 校验 + 断点续传 + 重试逻辑。
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
WOLF_DEC_DIR = ROOT / "resources" / "wolf_dec"

_UBERWOLF_TAG = "v0.6.4"
_UBERWOLF_CLI_URL = (
    f"https://github.com/Sinflower/UberWolf/releases/download/{_UBERWOLF_TAG}/"
    "UberWolfCli.exe"
)
_UBERWOLF_CLI_SHA256 = "0c9645733ae9544df11ee0c859a7f2cb51aa547d5d13f7935cb480bdab96fb3a"


def _load_build_full() -> ModuleType:
    """跟 fetch_translation_font.py/fetch_unity_mod_assets.py 用同一种方式动态
    加载——scripts/ 不是包，没有 __init__.py，不为了复用一个 download() 函数就把它
    改造成包结构。"""
    spec = importlib.util.spec_from_file_location(
        "build_full", Path(__file__).resolve().parent / "build_full.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_build_full = _load_build_full()
download = _build_full.download


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-redownload", action="store_true")
    args = parser.parse_args(argv)

    WOLF_DEC_DIR.mkdir(parents=True, exist_ok=True)
    download(
        _UBERWOLF_CLI_URL,
        WOLF_DEC_DIR / "UberWolfCli.exe",
        expected_sha256=_UBERWOLF_CLI_SHA256,
        force=args.force_redownload,
    )
    print(f"[fetch_wolf_dec] 就绪：{WOLF_DEC_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
