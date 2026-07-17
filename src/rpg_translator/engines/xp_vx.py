from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rpg_translator.core.ir import EngineName
from rpg_translator.engines._rgss_common import RGSSAdapterBase

# XP/VX は VX Ace と同族の Ruby Marshal 形式。M4.5 で洗い出したリスク
# （spec 6.3 節参照）のうち、下記 2 点は M4.9 で真実 XP 工程（GitHub 上の
# GPL-3.0 同人游戏 torresflo/Pokemon-Obsidian）実測により決着が付いた：
#   1. RPG::xxx クラスの実際のフィールド名 → VX Ace と一致することを確認済み
#      （@name/@description/@parameters などそのまま通用した）。
#   2. 文字列エンコーディング → 実際には「Shift-JIS かも」という以前の予想より
#      根本的な問題だった。XP が使う古い Ruby（1.8、文字列にエンコーディング
#      概念が無い）で marshal した文字列は、rubymarshal だと自動デコードされず
#      生の `bytes` のまま返ってくる（VX Ace は Ruby 1.9+ で文字列に ivar
#      エンコーディング標記が付くので rubymarshal が自動デコードしてくれるが、
#      XP/VX はこの標記が無い）。旧コードはこれに Python の `str()` を直接
#      呼んでいて、抽出される"テキスト"が実は `b'...'` という repr 文字列
#      だった（実機データで初めて発覚、合成サンプルでは検出不能だった）。
#      `_rgss_common.py` の `rv_str`/`_encode_like` で UTF-8 優先・cp932
#      フォールバックのデコード/エンコードに直した（VX は今回は実機無しだが
#      XP と同じ Ruby 系列なので同じ対応で扱っている）。
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
