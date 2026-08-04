"""下载 Unity 运行时外挂翻译需要的 BepInEx + XUnity.AutoTranslator 四种变体
（Mono/IL2CPP × x86/x64），解压合并到 resources/unity_mod/。设计背景见
docs/superpowers/specs/2026-08-04-unity-runtime-translation-design.md。

用法：.venv\\Scripts\\python.exe scripts\\fetch_unity_mod_assets.py
产出：resources/unity_mod/{mono_x86,mono_x64,il2cpp_x86,il2cpp_x64}/

不进 git（体积大、第三方二进制），resources/unity_mod/ 已加进 .gitignore。跟
build_full.py 一样不在自动化测试/CI 里跑（要联网下载），自动化测试只覆盖内部
纯函数 extract_all。下载复用 build_full.py 已有的 sha256 校验 + 断点续传 +
重试逻辑，不重新造轮子。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import stat
import zipfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
UNITY_MOD_DIR = ROOT / "resources" / "unity_mod"


def _load_build_full() -> ModuleType:
    """跟 tests/test_build_full.py 用同一种方式动态加载——scripts/ 不是包，
    没有 __init__.py，不为了复用几个函数就把它改造成包结构。"""
    spec = importlib.util.spec_from_file_location(
        "build_full", Path(__file__).resolve().parent / "build_full.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_build_full = _load_build_full()
download = _build_full.download
ChecksumMismatchError = _build_full.ChecksumMismatchError

_BEPINEX_BASE_URL = os.environ.get(
    "BEPINEX_RELEASE_BASE_URL", "https://github.com/BepInEx/BepInEx/releases/download"
)
_XUNITY_BASE_URL = os.environ.get(
    "XUNITY_AUTOTRANSLATOR_RELEASE_BASE_URL",
    "https://github.com/bbepis/XUnity.AutoTranslator/releases/download",
)

_BEPINEX_MONO_TAG = "v5.4.23.5"
_BEPINEX_IL2CPP_TAG = "v6.0.0-pre.2"
_XUNITY_TAG = "v5.6.1"

# XUnity zip 里除了 XUnity.AutoTranslator/ 还带一个跟翻译无关的独立插件
# XUnity.ResourceRedirector/（贴图/字体资源重定向）。BepInEx 会自动加载
# plugins/ 下所有 dll，带着它等于多启用一个本设计不需要的插件，不符合
# "侵入性最低"，合并时显式跳过。
_XUNITY_SKIP_PREFIXES = ("BepInEx/plugins/XUnity.ResourceRedirector/",)

# sha256 是 2026-08-04 实测下载后核对过的值（见 resources/unity_mod/SOURCES.md），
# 后续如果改动上面的 tag/文件名，要重新核对并回填。
_ASSETS: dict[str, list[tuple[str, str | None, tuple[str, ...]]]] = {
    "mono_x64": [
        (
            f"{_BEPINEX_BASE_URL}/{_BEPINEX_MONO_TAG}/BepInEx_win_x64_5.4.23.5.zip",
            "82f9878551030f54657792c0740d9d51a09500eeae1fba21106b0c441e6732c4",
            (),
        ),
        (
            f"{_XUNITY_BASE_URL}/{_XUNITY_TAG}/XUnity.AutoTranslator-BepInEx-5.6.1.zip",
            "fbb7d1bbe2c7cc168da6dccbc500fb74786a85a548f52495c8a1592ac46407f5",
            _XUNITY_SKIP_PREFIXES,
        ),
    ],
    "mono_x86": [
        (
            f"{_BEPINEX_BASE_URL}/{_BEPINEX_MONO_TAG}/BepInEx_win_x86_5.4.23.5.zip",
            "37651c79e40d6f909572a4f461ac25350bb3ef8fe7fbd29f1aa8791a33b84c82",
            (),
        ),
        (
            f"{_XUNITY_BASE_URL}/{_XUNITY_TAG}/XUnity.AutoTranslator-BepInEx-5.6.1.zip",
            "fbb7d1bbe2c7cc168da6dccbc500fb74786a85a548f52495c8a1592ac46407f5",
            _XUNITY_SKIP_PREFIXES,
        ),
    ],
    "il2cpp_x64": [
        (
            f"{_BEPINEX_BASE_URL}/{_BEPINEX_IL2CPP_TAG}/BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip",
            "616ec7eb06cf11b2a0000e8fcef04d1b12bb58e84a2e0bdac9523234fc193ceb",
            (),
        ),
        (
            f"{_XUNITY_BASE_URL}/{_XUNITY_TAG}/XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip",
            "9d6b26e9d4957459bdb64b6d4852edb39cd5e8d31c28e0a157cefd6510ada811",
            _XUNITY_SKIP_PREFIXES,
        ),
    ],
    "il2cpp_x86": [
        (
            f"{_BEPINEX_BASE_URL}/{_BEPINEX_IL2CPP_TAG}/BepInEx-Unity.IL2CPP-win-x86-6.0.0-pre.2.zip",
            "cfef3a1e946dac5db8b9de4de1a922f47584dd775da32863f36762fbaad80f19",
            (),
        ),
        (
            f"{_XUNITY_BASE_URL}/{_XUNITY_TAG}/XUnity.AutoTranslator-BepInEx-IL2CPP-5.6.1.zip",
            "9d6b26e9d4957459bdb64b6d4852edb39cd5e8d31c28e0a157cefd6510ada811",
            _XUNITY_SKIP_PREFIXES,
        ),
    ],
}


def extract_all(
    zip_path: Path, dest_dir: Path, *, skip_prefixes: tuple[str, ...] = ()
) -> list[Path]:
    """解压 zip 全部内容到 dest_dir，保留内部目录结构（跟 build_full.py 的
    extract_members 不同——那个按后缀摊平，这里 BepInEx/XUnity 的目录层级本身
    有意义，必须原样保留）。同名文件后解压的覆盖先解压的，用来把 XUnity 插件
    合并进已经解压好的 BepInEx 目录。skip_prefixes 匹配的成员直接跳过，不落盘。

    BepInEx 官方 zip 里 .doorstop_version 这个文件本身就是只读的（Unix 权限位
    444，解压工具会把这个属性带到 Windows 的只读属性上）——重新跑这个脚本覆盖
    已存在的同名文件时，Windows 会因为目标文件只读直接拒绝写入（实测复现过
    PermissionError）。写入前先把已存在的目标文件改成可写，不假设解压出来的
    文件一定是普通权限。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if any(info.filename.startswith(prefix) for prefix in skip_prefixes):
                continue
            target = dest_dir / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.chmod(stat.S_IWRITE | stat.S_IREAD)
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
            extracted.append(target)
    return extracted


def _fetch_variant(
    variant: str,
    assets: list[tuple[str, str | None, tuple[str, ...]]],
    work_dir: Path,
    force: bool,
) -> None:
    dest_dir = UNITY_MOD_DIR / variant
    for url, expected_sha256, skip_prefixes in assets:
        zip_name = url.rsplit("/", 1)[-1]
        zip_path = work_dir / zip_name
        download(url, zip_path, expected_sha256=expected_sha256, force=force)
        extract_all(zip_path, dest_dir, skip_prefixes=skip_prefixes)
    print(f"[fetch_unity_mod_assets] {variant} 就绪：{dest_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=UNITY_MOD_DIR / "_downloads")
    parser.add_argument("--force-redownload", action="store_true")
    args = parser.parse_args(argv)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    for variant, assets in _ASSETS.items():
        _fetch_variant(variant, assets, args.work_dir, args.force_redownload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
