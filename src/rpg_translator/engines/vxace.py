from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rpg_translator.core.ir import EngineName
from rpg_translator.engines._rgss_common import RGSSAdapterBase

# RPG::Actors/Classes/... の実データを直接検証できる VX Ace プロジェクトが手元にないため、
# フィールド名は公開の RGSS3 参照実装（bluepixelmike/rpg-maker-rgss、非公式コミュニティ製）
# から確認したもの。実際のゲームで挙動が違えばここを直す必要がある。


class VXAceAdapter(RGSSAdapterBase):
    engine_name: ClassVar[EngineName] = "vxace"
    data_dir: ClassVar[str] = "Data"
    file_extension: ClassVar[str] = ".rvdata2"
    database_files: ClassVar[list[str]] = [
        "Actors.rvdata2",
        "Classes.rvdata2",
        "Skills.rvdata2",
        "Items.rvdata2",
        "Weapons.rvdata2",
        "Armors.rvdata2",
        "Enemies.rvdata2",
        "States.rvdata2",
    ]

    @staticmethod
    def detect(project_dir: Path) -> bool:
        return (project_dir / "Data" / "Actors.rvdata2").is_file() or (
            project_dir / "Game.rvproj2"
        ).is_file()
