from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build.py"


def _load_build() -> ModuleType:
    """scripts/ 不是包，按路径动态加载，跟 tests/test_build_full.py 同一种方式。"""
    spec = importlib.util.spec_from_file_location("build", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()


def test_bundle_unity_mod_assets_skips_when_source_missing(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "DIST_APP_DIR", tmp_path / "dist" / "RPGTranslator")

    build._bundle_unity_mod_assets()

    assert not (tmp_path / "dist" / "RPGTranslator" / "resources" / "unity_mod").exists()
    assert "跳过" in capsys.readouterr().out


def test_bundle_unity_mod_assets_copies_present_variants_only(tmp_path: Path, monkeypatch):
    source_root = tmp_path / "resources" / "unity_mod"
    (source_root / "mono_x64" / "BepInEx" / "core").mkdir(parents=True)
    (source_root / "mono_x64" / "winhttp.dll").write_bytes(b"proxy")
    (source_root / "mono_x64" / "BepInEx" / "core" / "BepInEx.dll").write_bytes(b"core")
    # 故意只准备一个变体——il2cpp_x64 等目录不存在时该被跳过，不报错。
    # _downloads/ 和 SOURCES.md 是下载缓存/来源记录，不该被打包进产物。
    (source_root / "_downloads").mkdir()
    (source_root / "_downloads" / "cache.zip").write_bytes(b"raw-zip-cache")
    (source_root / "SOURCES.md").write_text("source notes", encoding="utf-8")

    dist_app_dir = tmp_path / "dist" / "RPGTranslator"
    dist_app_dir.mkdir(parents=True)
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "DIST_APP_DIR", dist_app_dir)

    build._bundle_unity_mod_assets()

    dest_root = dist_app_dir / "resources" / "unity_mod"
    assert (dest_root / "mono_x64" / "winhttp.dll").read_bytes() == b"proxy"
    assert (dest_root / "mono_x64" / "BepInEx" / "core" / "BepInEx.dll").read_bytes() == b"core"
    assert not (dest_root / "il2cpp_x64").exists()
    assert not (dest_root / "_downloads").exists()
    assert not (dest_root / "SOURCES.md").exists()
