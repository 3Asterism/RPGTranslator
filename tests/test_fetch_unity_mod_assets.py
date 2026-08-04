from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path
from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fetch_unity_mod_assets.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_unity_mod_assets", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_unity_mod_assets = _load_module()


def _make_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def test_extract_all_preserves_directory_structure(tmp_path: Path):
    zip_path = tmp_path / "src.zip"
    _make_zip(
        zip_path,
        {
            "winhttp.dll": b"proxy",
            "BepInEx/core/BepInEx.dll": b"core",
        },
    )
    dest_dir = tmp_path / "out"

    extracted = fetch_unity_mod_assets.extract_all(zip_path, dest_dir)

    assert (dest_dir / "winhttp.dll").read_bytes() == b"proxy"
    assert (dest_dir / "BepInEx" / "core" / "BepInEx.dll").read_bytes() == b"core"
    assert len(extracted) == 2


def test_extract_all_overwrites_when_merging_second_zip(tmp_path: Path):
    dest_dir = tmp_path / "out"
    first = tmp_path / "first.zip"
    _make_zip(first, {"BepInEx/core/existing.dll": b"from-bepinex"})
    fetch_unity_mod_assets.extract_all(first, dest_dir)

    second = tmp_path / "second.zip"
    _make_zip(
        second,
        {
            "BepInEx/plugins/XUnity.AutoTranslator/plugin.dll": b"from-xunity",
        },
    )
    fetch_unity_mod_assets.extract_all(second, dest_dir)

    assert (dest_dir / "BepInEx" / "core" / "existing.dll").read_bytes() == b"from-bepinex"
    assert (dest_dir / "BepInEx" / "plugins" / "XUnity.AutoTranslator" / "plugin.dll").read_bytes() == b"from-xunity"


def test_extract_all_skips_paths_matching_skip_prefixes(tmp_path: Path):
    """XUnity 插件包里带一个跟翻译无关的独立插件 XUnity.ResourceRedirector/，
    合并时要能显式过滤掉，不能假设 XUnity 包解压出来只有 AutoTranslator 一个
    东西（见设计文档"实测拉取后确认的几个点"）。"""
    zip_path = tmp_path / "xunity.zip"
    _make_zip(
        zip_path,
        {
            "BepInEx/plugins/XUnity.AutoTranslator/plugin.dll": b"keep",
            "BepInEx/plugins/XUnity.ResourceRedirector/redirector.dll": b"skip",
        },
    )
    dest_dir = tmp_path / "out"

    extracted = fetch_unity_mod_assets.extract_all(
        zip_path, dest_dir, skip_prefixes=("BepInEx/plugins/XUnity.ResourceRedirector/",)
    )

    assert (dest_dir / "BepInEx" / "plugins" / "XUnity.AutoTranslator" / "plugin.dll").is_file()
    assert not (dest_dir / "BepInEx" / "plugins" / "XUnity.ResourceRedirector").exists()
    assert len(extracted) == 1


def test_extract_all_overwrites_readonly_destination_file(tmp_path: Path):
    """BepInEx 官方 zip 里 .doorstop_version 这个文件本身是只读的（Unix 权限位
    444）；重新跑一遍脚本覆盖已经落地的同名只读文件时，Windows 会因为目标文件
    只读直接拒绝写入——这是实测复现过的真实 PermissionError，不是假设场景。"""
    dest_dir = tmp_path / "out"
    dest_dir.mkdir()
    existing = dest_dir / ".doorstop_version"
    existing.write_bytes(b"old")
    existing.chmod(0o444)

    zip_path = tmp_path / "src.zip"
    _make_zip(zip_path, {".doorstop_version": b"new"})

    fetch_unity_mod_assets.extract_all(zip_path, dest_dir)

    assert existing.read_bytes() == b"new"


def test_all_four_variants_have_matching_asset_urls_for_their_backend():
    """静态一致性检查：mono_* 两个变体应该用同一个 XUnity Mono 插件包 URL，
    il2cpp_* 两个变体应该用同一个 XUnity IL2CPP 插件包 URL——手误改错其中一个
    tag/文件名的话，这个测试能第一时间抓出来，而不是要等真的联网下载才发现。"""
    mono_xunity_urls = {
        url for variant in ("mono_x64", "mono_x86") for url, _, _ in fetch_unity_mod_assets._ASSETS[variant] if "XUnity" in url
    }
    il2cpp_xunity_urls = {
        url
        for variant in ("il2cpp_x64", "il2cpp_x86")
        for url, _, _ in fetch_unity_mod_assets._ASSETS[variant]
        if "XUnity" in url
    }
    assert len(mono_xunity_urls) == 1
    assert len(il2cpp_xunity_urls) == 1
    assert mono_xunity_urls != il2cpp_xunity_urls
    assert "IL2CPP" not in next(iter(mono_xunity_urls))
    assert "IL2CPP" in next(iter(il2cpp_xunity_urls))
