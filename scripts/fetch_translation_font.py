"""下载注入 RPG Maker MV/MZ 游戏时用来替换主字体的中文字体（霞鹜文楷），
解压/落盘到 resources/fonts/。设计背景见 engines/mv_mz.py 的 _patch_font
说明——日文游戏原版字体常常不含简体中文专有字形（比如"么"），而这些引擎的
Canvas 文字渲染对 System.json 里 fallbackFonts 多字体链的支持不可靠（实测
改这个字段不生效），只能直接把主字体本身换成一份中文覆盖完整的字体。

用法：.venv\\Scripts\\python.exe scripts\\fetch_translation_font.py
产出：resources/fonts/LXGWWenKai-Regular.ttf、resources/fonts/OFL.txt

不进 git（体积大、第三方二进制），resources/fonts/ 已加进 .gitignore。跟
build_full.py/fetch_unity_mod_assets.py 一样不在自动化测试/CI 里跑（要联网
下载），下载复用 build_full.py 已有的 sha256 校验 + 断点续传 + 重试逻辑。
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "resources" / "fonts"

_LXGW_WENKAI_TAG = "v1.522"
_LXGW_WENKAI_URL = (
    f"https://github.com/lxgw/LxgwWenKai/releases/download/{_LXGW_WENKAI_TAG}/"
    "LXGWWenKai-Regular.ttf"
)
_LXGW_WENKAI_SHA256 = "39ad71264b588165b469e35e6afb162a378dacd1f95348160240ba9038ac3009"
_OFL_URL = "https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/OFL.txt"
_OFL_SHA256 = "1a25e35da1031c6c3436fde545bb9cb5aca954e9873afe510c834b8b79bd21a0"


def _load_build_full() -> ModuleType:
    """跟 fetch_unity_mod_assets.py 用同一种方式动态加载——scripts/ 不是包，
    没有 __init__.py，不为了复用一个 download() 函数就把它改造成包结构。"""
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

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    download(
        _LXGW_WENKAI_URL,
        FONTS_DIR / "LXGWWenKai-Regular.ttf",
        expected_sha256=_LXGW_WENKAI_SHA256,
        force=args.force_redownload,
    )
    download(
        _OFL_URL,
        FONTS_DIR / "OFL.txt",
        expected_sha256=_OFL_SHA256,
        force=args.force_redownload,
    )
    print(f"[fetch_translation_font] 就绪：{FONTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
