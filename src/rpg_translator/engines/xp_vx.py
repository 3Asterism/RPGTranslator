from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rpg_translator.core.ir import EngineName
from rpg_translator.engines._rgss_common import RGSSAdapterBase

# XP/VX は VX Ace と同族の Ruby Marshal 形式だが、実データで検証できていない。
# 特に以下 2 点は M4.5 で最初に確認すべきリスク（spec 6.3 節参照）：
#   1. RPG::xxx クラスの実際のフィールド名が VX Ace と完全に同じとは限らない
#      （VX Ace は VX を土台にデータ構造を作り直しているため）
#   2. 文字列エンコーディングが UTF-8 ではなく Shift-JIS の可能性がある
#      （rubymarshal は Marshal の ivar 属性に "E"/"encoding" が無ければ既定で
#      latin1 にフォールバックする実装なので、古い Ruby 1.8 世代のバイナリだと
#      文字化けする恐れがある——実物のセーブデータで確認が必要）
_DATABASE_FILES_COMMON = [
    "Actors",
    "Classes",
    "Skills",
    "Items",
    "Weapons",
    "Armors",
    "Enemies",
    "States",
]


class XPAdapter(RGSSAdapterBase):
    engine_name: ClassVar[EngineName] = "xp"
    data_dir: ClassVar[str] = "Data"
    file_extension: ClassVar[str] = ".rxdata"
    database_files: ClassVar[list[str]] = [f"{name}.rxdata" for name in _DATABASE_FILES_COMMON]

    @staticmethod
    def detect(project_dir: Path) -> bool:
        return (project_dir / "Data" / "Actors.rxdata").is_file() or (
            project_dir / "Game.rxproj"
        ).is_file()


class VXAdapter(RGSSAdapterBase):
    engine_name: ClassVar[EngineName] = "vx"
    data_dir: ClassVar[str] = "Data"
    file_extension: ClassVar[str] = ".rvdata"
    database_files: ClassVar[list[str]] = [f"{name}.rvdata" for name in _DATABASE_FILES_COMMON]

    @staticmethod
    def detect(project_dir: Path) -> bool:
        return (project_dir / "Data" / "Actors.rvdata").is_file() or (
            project_dir / "Game.rvproj"
        ).is_file()
