from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rpg_translator.unity.deploy import (
    UnsupportedVariantError,
    deploy,
    remove,
)
from rpg_translator.unity.detect import UnityTarget


def _make_variant_dir(resources_root: Path, variant: str) -> Path:
    variant_dir = resources_root / "unity_mod" / variant
    (variant_dir / "BepInEx" / "core").mkdir(parents=True)
    (variant_dir / "winhttp.dll").write_bytes(b"fake-doorstop-proxy")
    (variant_dir / "doorstop_config.ini").write_text("[General]\nenabled = true\n", encoding="utf-8")
    (variant_dir / "BepInEx" / "core" / "BepInEx.dll").write_bytes(b"fake-bepinex-core")
    return variant_dir


def _make_target(tmp_path: Path, backend: str = "mono", arch: str = "x64") -> UnityTarget:
    game_dir = tmp_path / "Game"
    game_dir.mkdir()
    exe_path = game_dir / "Game.exe"
    exe_path.write_bytes(b"fake-exe")
    data_dir = game_dir / "Game_Data"
    data_dir.mkdir()
    return UnityTarget(exe_path=exe_path, data_dir=data_dir, backend=backend, arch=arch)


def test_deploy_copies_variant_files_into_game_dir(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path, backend="mono", arch="x64")

    result = deploy(target, shim_port=54321, resources_root=resources_root)

    game_dir = target.exe_path.parent
    assert (game_dir / "winhttp.dll").read_bytes() == b"fake-doorstop-proxy"
    assert (game_dir / "doorstop_config.ini").is_file()
    assert (game_dir / "BepInEx" / "core" / "BepInEx.dll").is_file()
    assert result.manifest_path.is_file()
    assert "winhttp.dll" in result.deployed_files


def test_deploy_writes_autotranslator_config_pointing_at_shim_port(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)

    result = deploy(target, shim_port=54321, resources_root=resources_root)

    content = result.config_path.read_text(encoding="utf-8")
    assert "Endpoint=CustomTranslate" in content
    assert "http://127.0.0.1:54321/translate" in content
    # [General] 段必须显式写目标/源语言——不写的话 XUnity 会用自己的默认值
    # （Language=en, FromLanguage=ja）补齐，一个中文本地化工具的输出会变成
    # 英文，不是留空就没事（真实复现过的问题，不是假设）。
    assert "Language=zh-CN" in content
    assert "FromLanguage=ja" in content


def test_deploy_raises_for_variant_combo_outside_the_known_mapping(tmp_path: Path):
    """UnityTarget.backend/arch 是 Literal 类型，正常经 detect_unity() 产出的值
    只会是 mono/il2cpp × x86/x64 这 4 种、且 _VARIANT_DIR 已经全部覆盖——这里
    构造一个类型注解之外的值（Python 不在运行时强制 Literal），模拟"某处传入
    了映射表之外的组合"这种防御性场景，不是真实调用路径会自然触发的情况。"""
    resources_root = tmp_path / "resources_root"
    target = _make_target(tmp_path, backend="unknown_backend", arch="x64")  # type: ignore[arg-type]

    with pytest.raises(UnsupportedVariantError):
        deploy(target, shim_port=1, resources_root=resources_root)


def test_deploy_raises_file_not_found_when_variant_dir_missing(tmp_path: Path):
    """合法组合（比如 mono/x86）但对应素材目录还没准备好（没跑过 fetch 脚本）：
    FileNotFoundError，不是笼统地失败在别处。"""
    resources_root = tmp_path / "resources_root"
    target = _make_target(tmp_path, backend="mono", arch="x86")

    with pytest.raises(FileNotFoundError):
        deploy(target, shim_port=1, resources_root=resources_root)


def test_deploy_backs_up_preexisting_file_before_overwriting(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    (game_dir / "winhttp.dll").write_bytes(b"original-unrelated-winhttp")

    deploy(target, shim_port=1, resources_root=resources_root)

    backup = game_dir / ".rpg_translator_backup" / "unity_original" / "winhttp.dll"
    assert backup.read_bytes() == b"original-unrelated-winhttp"
    assert (game_dir / "winhttp.dll").read_bytes() == b"fake-doorstop-proxy"


def test_deploy_twice_does_not_overwrite_backup_with_our_own_first_deploy(tmp_path: Path):
    """第二次部署（比如换了 shim 端口重新部署）不能把第一次部署时已经写进去的
    我们自己的文件误当成"原文件"重新备份一遍，覆盖掉真正的原始备份。"""
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    (game_dir / "winhttp.dll").write_bytes(b"original-unrelated-winhttp")

    deploy(target, shim_port=1, resources_root=resources_root)
    deploy(target, shim_port=2, resources_root=resources_root)

    backup = game_dir / ".rpg_translator_backup" / "unity_original" / "winhttp.dll"
    assert backup.read_bytes() == b"original-unrelated-winhttp"


def test_deploy_does_not_backup_config_that_we_generated_ourselves(tmp_path: Path):
    """AutoTranslatorConfig.ini 是我们自己每次部署都生成/覆写的（见设计文档），
    第二次部署不该把第一次我们自己写的那份错当成"游戏原有文件"备份下来。"""
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent

    deploy(target, shim_port=1, resources_root=resources_root)
    deploy(target, shim_port=2, resources_root=resources_root)

    config_backup = game_dir / ".rpg_translator_backup" / "unity_original" / "BepInEx" / "config" / "AutoTranslatorConfig.ini"
    assert not config_backup.exists()
    content = (game_dir / "BepInEx" / "config" / "AutoTranslatorConfig.ini").read_text(encoding="utf-8")
    assert "54321" not in content
    assert "http://127.0.0.1:2/translate" in content


def test_deploy_twice_on_clean_dir_never_creates_backup_of_our_own_mod_files(tmp_path: Path):
    """游戏目录本来就没有任何冲突文件时，重复部署（换端口）不该凭空生出一个
    backup 目录——没有什么是"原始文件"，我们自己上一次部署的文件不算。"""
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent

    deploy(target, shim_port=1, resources_root=resources_root)
    deploy(target, shim_port=2, resources_root=resources_root)

    assert not (game_dir / ".rpg_translator_backup").exists()


def test_deploy_overwrites_readonly_destination_file(tmp_path: Path):
    """BepInEx 官方 zip 里有些文件本身是只读的（比如 .doorstop_version，Unix
    权限位 444），shutil.copy2 部署时会把这个只读属性也带过去；第二次部署
    覆盖这类文件时，Windows 会因为目标只读直接拒绝写入——这是实测复现过的
    真实 PermissionError（见 scripts/fetch_unity_mod_assets.py 的同类修复），
    不是假设场景。"""
    resources_root = tmp_path / "resources_root"
    variant_dir = _make_variant_dir(resources_root, "mono_x64")
    (variant_dir / ".doorstop_version").write_text("1", encoding="utf-8")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent

    deploy(target, shim_port=1, resources_root=resources_root)
    (game_dir / ".doorstop_version").chmod(0o444)

    # 不该抛 PermissionError。
    deploy(target, shim_port=2, resources_root=resources_root)

    assert (game_dir / ".doorstop_version").read_text(encoding="utf-8") == "1"


def test_remove_restores_over_readonly_destination_and_deletes_readonly_backup(tmp_path: Path):
    """卸载还原同样可能撞见只读文件：还原目标只读、或者备份文件本身只读
    （游戏原始文件恰好是只读的，比如某些只读打包资源）都不该让 remove() 崩掉。"""
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    (game_dir / "winhttp.dll").write_bytes(b"original-readonly-winhttp")
    (game_dir / "winhttp.dll").chmod(0o444)

    deploy(target, shim_port=1, resources_root=resources_root)
    (game_dir / "winhttp.dll").chmod(0o444)  # 部署覆盖后的文件也标成只读，模拟最坏情况

    result = remove(game_dir)

    assert (game_dir / "winhttp.dll").read_bytes() == b"original-readonly-winhttp"
    assert "winhttp.dll" in result.restored


def test_remove_deletes_pure_additions_and_restores_backed_up_files(tmp_path: Path):
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    (game_dir / "winhttp.dll").write_bytes(b"original-unrelated-winhttp")

    deploy(target, shim_port=1, resources_root=resources_root)
    result = remove(game_dir)

    assert (game_dir / "winhttp.dll").read_bytes() == b"original-unrelated-winhttp"
    assert not (game_dir / "BepInEx" / "core" / "BepInEx.dll").exists()
    assert not (game_dir / "doorstop_config.ini").exists()
    assert "winhttp.dll" in result.restored
    assert "doorstop_config.ini" in result.removed
    assert not (game_dir / ".rpg_translator_unity" / "manifest.json").exists()


def test_remove_cleans_up_nested_empty_dirs_outside_bepinex(tmp_path: Path):
    """IL2CPP 变体在游戏根目录还铺了一整棵 dotnet/ 运行时目录（不在
    BepInEx/ 下）；remove() 删完文件后不该只清理 BepInEx/ 一个固定目录名，
    其它变体带来的空目录骨架也要清掉。"""
    resources_root = tmp_path / "resources_root"
    variant_dir = _make_variant_dir(resources_root, "mono_x64")
    (variant_dir / "dotnet" / "runtime").mkdir(parents=True)
    (variant_dir / "dotnet" / "runtime" / "coreclr.dll").write_bytes(b"fake-coreclr")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent

    deploy(target, shim_port=1, resources_root=resources_root)
    assert (game_dir / "dotnet" / "runtime" / "coreclr.dll").is_file()

    remove(game_dir)

    assert not (game_dir / "dotnet").exists()


def test_remove_without_prior_deploy_is_a_noop(tmp_path: Path):
    game_dir = tmp_path / "Game"
    game_dir.mkdir()

    result = remove(game_dir)

    assert result.removed == []
    assert result.restored == []


def test_remove_recovers_from_mid_loop_failure_without_corrupting_already_restored_files(
    tmp_path: Path, monkeypatch
):
    """remove() 中途失败（最现实的场景：用户没关游戏就点卸载，某个文件被游戏
    进程锁住报 PermissionError）之后重新跑一遍，不该把已经在第一次调用里成功
    还原的原始文件，误判成"备份没了、当纯新增文件"删掉。"""
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    (game_dir / "winhttp.dll").write_bytes(b"original-winhttp")
    (game_dir / "doorstop_config.ini").write_text("original-doorstop", encoding="utf-8")

    deploy(target, shim_port=1, resources_root=resources_root)

    import rpg_translator.unity.deploy as deploy_module

    real_copy2 = shutil.copy2
    call_count = {"n": 0}

    def flaky_copy2(src, dst, *a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise PermissionError("simulated: file locked by running game")
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(deploy_module.shutil, "copy2", flaky_copy2)
    with pytest.raises(PermissionError):
        remove(game_dir)
    monkeypatch.undo()  # 模拟"用户关掉游戏后重新点卸载"，这次不再故障注入

    remove(game_dir)

    assert (game_dir / "winhttp.dll").read_bytes() == b"original-winhttp"
    assert (game_dir / "doorstop_config.ini").read_text(encoding="utf-8") == "original-doorstop"


def test_remove_rejects_manifest_paths_that_escape_game_dir(tmp_path: Path):
    """manifest.json 是 remove() 唯一的操作依据，而它就摆在游戏目录里——如果
    游戏来源不可信（比如下载的游戏压缩包里预置了一份精心构造的
    manifest.json），未经校验直接拼接会被 "../.." 带出 game_dir 之外，造成
    任意文件删除。这里手动构造一份带越界路径的 manifest 模拟这个场景。"""
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent

    deploy(target, shim_port=1, resources_root=resources_root)

    outside_marker = tmp_path / "outside_marker.txt"
    outside_marker.write_text("should not be touched", encoding="utf-8")
    manifest_path = game_dir / ".rpg_translator_unity" / "manifest.json"
    manifest_path.write_text(
        json.dumps({"deployed_files": ["../outside_marker.txt", "winhttp.dll"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = remove(game_dir)

    assert outside_marker.read_text(encoding="utf-8") == "should not be touched"
    assert "winhttp.dll" in result.removed


def test_deploy_then_remove_round_trip_leaves_game_dir_as_before(tmp_path: Path):
    """端到端场景：一个原本干净的游戏目录，部署再卸载之后应该跟部署前完全
    一样（新增文件全部消失，没碰过的文件不受影响）。"""
    resources_root = tmp_path / "resources_root"
    _make_variant_dir(resources_root, "mono_x64")
    target = _make_target(tmp_path)
    game_dir = target.exe_path.parent
    before = sorted(p.relative_to(game_dir) for p in game_dir.rglob("*") if p.is_file())

    deploy(target, shim_port=1, resources_root=resources_root)
    remove(game_dir)

    after = sorted(p.relative_to(game_dir) for p in game_dir.rglob("*") if p.is_file())
    assert after == before
