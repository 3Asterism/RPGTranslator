from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from rpg_translator.unity.detect import UnityTarget

_BACKUP_DIR_NAME = ".rpg_translator_backup"
_STATE_DIR_NAME = ".rpg_translator_unity"
_MANIFEST_NAME = "manifest.json"
_CONFIG_RELATIVE_PATH = Path("BepInEx") / "config" / "AutoTranslatorConfig.ini"

# Mono 用稳定版 BepInEx 5，IL2CPP 只能用 BepInEx 6 pre-release（v5 稳定版没有
# IL2CPP 支持）——见设计文档"已知限制"一节，不是这里能解决的问题。
_VARIANT_DIR = {
    ("mono", "x86"): "mono_x86",
    ("mono", "x64"): "mono_x64",
    ("il2cpp", "x86"): "il2cpp_x86",
    ("il2cpp", "x64"): "il2cpp_x64",
}


@dataclass(frozen=True)
class DeployResult:
    manifest_path: Path
    config_path: Path
    deployed_files: list[str]


@dataclass(frozen=True)
class RemoveResult:
    removed: list[str]
    restored: list[str]


class UnsupportedVariantError(ValueError):
    pass


def _variant_dir(target: UnityTarget, resources_root: Path) -> Path:
    key = (target.backend, target.arch)
    if key not in _VARIANT_DIR:
        raise UnsupportedVariantError(f"不支持的组合：{key}")
    return resources_root / "unity_mod" / _VARIANT_DIR[key]


def _game_dir(target: UnityTarget) -> Path:
    return target.exe_path.parent


def _backup_dir(game_dir: Path) -> Path:
    return game_dir / _BACKUP_DIR_NAME / "unity_original"


def _state_dir(game_dir: Path) -> Path:
    return game_dir / _STATE_DIR_NAME


def _manifest_path(game_dir: Path) -> Path:
    return _state_dir(game_dir) / _MANIFEST_NAME


def _to_posix(rel: Path) -> str:
    return str(rel).replace("\\", "/")


def _write_config(config_path: Path, shim_port: int) -> None:
    """BepInEx/config/AutoTranslatorConfig.ini 是社区广泛验证过的实际路径，
    但没有被 XUnity 第一方 README 逐字确认过——第一次真机部署要单独验证 XUnity
    启动时是读取这份已存在的 ini 还是会用默认值覆盖它（见设计文档"实测拉取后
    确认的几个点"）。"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "[Service]\n"
        "Endpoint=CustomTranslate\n"
        "\n"
        "[Custom]\n"
        f"Url=http://127.0.0.1:{shim_port}/translate\n",
        encoding="utf-8",
    )


def _load_previously_deployed(game_dir: Path) -> set[str]:
    """读上一次部署（如果有）落下的 manifest，返回它记录过的相对路径集合。
    用来判断"这个文件是我们自己上次部署放的"还是"游戏本来就有的原始文件"——
    只有后者才值得备份。没有这个区分，第二次部署（比如换了 shim 端口）会把
    第一次部署时我们自己写的文件误当成"原文件"备份下来，backup 目录名不副实，
    remove() 也会照着这份假"原文件"去还原。"""
    manifest_path = _manifest_path(game_dir)
    if not manifest_path.is_file():
        return set()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return set(manifest.get("deployed_files", []))


def _backup_if_genuinely_original(
    dest: Path, rel_posix: str, backup_dir: Path, previously_deployed: set[str]
) -> None:
    if rel_posix in previously_deployed:
        return
    if not dest.is_file():
        return
    backup_dest = backup_dir / rel_posix
    if backup_dest.exists():
        return
    backup_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dest, backup_dest)


def deploy(target: UnityTarget, shim_port: int, resources_root: Path) -> DeployResult:
    variant_dir = _variant_dir(target, resources_root)
    if not variant_dir.is_dir():
        raise FileNotFoundError(
            f"找不到 mod 素材目录 {variant_dir}，请先跑 scripts/fetch_unity_mod_assets.py"
        )
    game_dir = _game_dir(target)
    backup_dir = _backup_dir(game_dir)
    previously_deployed = _load_previously_deployed(game_dir)

    deployed: list[str] = []
    for src in sorted(variant_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(variant_dir)
        rel_posix = _to_posix(rel)
        dest = game_dir / rel
        _backup_if_genuinely_original(dest, rel_posix, backup_dir, previously_deployed)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        deployed.append(rel_posix)

    config_path = game_dir / _CONFIG_RELATIVE_PATH
    config_rel = _to_posix(_CONFIG_RELATIVE_PATH)
    _backup_if_genuinely_original(config_path, config_rel, backup_dir, previously_deployed)
    _write_config(config_path, shim_port)
    if config_rel not in deployed:
        deployed.append(config_rel)

    manifest_path = _manifest_path(game_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"deployed_files": deployed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return DeployResult(manifest_path=manifest_path, config_path=config_path, deployed_files=deployed)


def _cleanup_empty_dirs(path: Path) -> None:
    if not path.is_dir():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()
    if not any(path.iterdir()):
        path.rmdir()


def remove(game_dir: Path) -> RemoveResult:
    manifest_path = _manifest_path(game_dir)
    if not manifest_path.is_file():
        return RemoveResult(removed=[], restored=[])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backup_dir = _backup_dir(game_dir)

    removed: list[str] = []
    restored: list[str] = []
    for rel in manifest.get("deployed_files", []):
        dest = game_dir / rel
        backup_src = backup_dir / rel
        if backup_src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_src, dest)
            backup_src.unlink()
            restored.append(rel)
        elif dest.is_file():
            dest.unlink()
            removed.append(rel)

    manifest_path.unlink()
    _cleanup_empty_dirs(game_dir / "BepInEx")
    _cleanup_empty_dirs(backup_dir)
    _cleanup_empty_dirs(_state_dir(game_dir))
    return RemoveResult(removed=removed, restored=restored)
