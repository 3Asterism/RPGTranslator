from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, ClassVar

from rubymarshal.classes import RubyObject

from rpg_translator.codec.rvdata2_codec import read_rvdata2, write_rvdata2
from rpg_translator.core.ir import EngineName, TextUnit, compute_text_unit_id
from rpg_translator.engines.base import EngineAdapter

# RPG::Actors/Classes/... の実データを直接検証できる VX Ace プロジェクトが手元にないため、
# フィールド名は公開の RGSS3 参照実装（bluepixelmike/rpg-maker-rgss、非公式コミュニティ製）
# から確認したもの。実際のゲームで挙動が違えばここを直す必要がある（M4.5 は同種の
# rubymarshal 検証を XP/VX でも繰り返す）。
_DATABASE_FILES = [
    "Actors.rvdata2",
    "Classes.rvdata2",
    "Skills.rvdata2",
    "Items.rvdata2",
    "Weapons.rvdata2",
    "Armors.rvdata2",
    "Enemies.rvdata2",
    "States.rvdata2",
]
_DATABASE_TEXT_FIELDS = ["@name", "@nickname", "@description", "@note", "@message1", "@message2"]
_MAP_FILE_RE = re.compile(r"^Map\d{3}\.rvdata2$")
_PURE_TAG_NOTE_RE = re.compile(r"^(\s*<[^<>\r\n]+>\s*)+$")


def _is_pure_tag_note(text: str) -> bool:
    return bool(_PURE_TAG_NOTE_RE.match(text))


def _rv_get(obj: Any, key: Any) -> Any:
    if isinstance(obj, RubyObject):
        return obj.attributes[key]
    return obj[key]


def _rv_set(obj: Any, key: Any, value: Any) -> None:
    if isinstance(obj, RubyObject):
        obj.attributes[key] = value
    else:
        obj[key] = value


def _parse_locator(locator: str) -> list[Any]:
    segments: list[Any] = []
    for seg in locator.split("/"):
        if seg.startswith("@"):
            segments.append(seg)
        elif seg.lstrip("-").isdigit():
            segments.append(int(seg))
        else:
            segments.append(seg)
    return segments


def _locator_get(root: Any, locator: str) -> Any:
    cur = root
    for seg in _parse_locator(locator):
        cur = _rv_get(cur, seg)
    return cur


def _locator_set(root: Any, locator: str, value: Any) -> None:
    segments = _parse_locator(locator)
    cur = root
    for seg in segments[:-1]:
        cur = _rv_get(cur, seg)
    _rv_set(cur, segments[-1], value)


class _PendingUnit:
    __slots__ = ("locator", "source_text", "context_group")

    def __init__(self, locator: str, source_text: str, context_group: str):
        self.locator = locator
        self.source_text = source_text
        self.context_group = context_group


def _extract_command_list(
    commands: list[RubyObject], path_prefix: str, group: str
) -> list[_PendingUnit]:
    found: list[_PendingUnit] = []
    for i, cmd in enumerate(commands):
        code = cmd.attributes.get("@code")
        params = cmd.attributes.get("@parameters", [])
        if code in (401, 405):
            if params and str(params[0]):
                found.append(_PendingUnit(f"{path_prefix}/{i}/@parameters/0", str(params[0]), group))
        elif code == 102:
            choices = params[0] if params else []
            for ci, choice in enumerate(choices):
                if str(choice):
                    found.append(
                        _PendingUnit(f"{path_prefix}/{i}/@parameters/0/{ci}", str(choice), group)
                    )
        elif code == 320:
            if len(params) > 1 and str(params[1]):
                found.append(_PendingUnit(f"{path_prefix}/{i}/@parameters/1", str(params[1]), group))
        # code 101 はヘッダーのみで VX Ace には話者名パラメータが無い（MZ 独自）ので何もしない
        # 108/408 (Comment)・355/655 (Script) はデフォルトで無視（MV/MZ と同じ方針）
    return found


class VXAceAdapter(EngineAdapter):
    engine_name: ClassVar[EngineName] = "vxace"
    data_dir: ClassVar[str] = "Data"

    @staticmethod
    def detect(project_dir: Path) -> bool:
        return (project_dir / "Data" / "Actors.rvdata2").is_file() or (
            project_dir / "Game.rvproj2"
        ).is_file()

    def extract(self, project_dir: Path) -> list[TextUnit]:
        data_root = project_dir / self.data_dir
        pending: list[_PendingUnit] = []

        for map_file in sorted(data_root.glob("Map*.rvdata2")):
            if not _MAP_FILE_RE.match(map_file.name):
                continue
            rel_path = f"{self.data_dir}/{map_file.name}"
            game_map = read_rvdata2(map_file)
            events = game_map.attributes.get("@events", {})
            for event_id, event in events.items():
                if event is None:
                    continue
                pages = event.attributes.get("@pages", [])
                for page_idx, page in enumerate(pages):
                    group = f"{rel_path}:@events/{event_id}/@pages/{page_idx}"
                    path_prefix = f"@events/{event_id}/@pages/{page_idx}/@list"
                    pending.extend(
                        _extract_command_list(page.attributes.get("@list", []), path_prefix, group)
                    )

        common_events_file = data_root / "CommonEvents.rvdata2"
        if common_events_file.is_file():
            rel_path = f"{self.data_dir}/CommonEvents.rvdata2"
            common_events = read_rvdata2(common_events_file)
            for ce_idx, ce in enumerate(common_events):
                if ce is None:
                    continue
                group = f"{rel_path}:{ce_idx}"
                path_prefix = f"{ce_idx}/@list"
                pending.extend(
                    _extract_command_list(ce.attributes.get("@list", []), path_prefix, group)
                )

        units = self._pending_to_units(pending)

        for db_filename in _DATABASE_FILES:
            db_file = data_root / db_filename
            if not db_file.is_file():
                continue
            rel_path = f"{self.data_dir}/{db_filename}"
            records = read_rvdata2(db_file)
            units.extend(self._extract_database_file(records, rel_path))

        return units

    def _extract_database_file(self, records: list[Any], rel_path: str) -> list[TextUnit]:
        units: list[TextUnit] = []
        for idx, record in enumerate(records):
            if record is None or not isinstance(record, RubyObject):
                continue
            record_name = str(record.attributes.get("@name", ""))
            for field in _DATABASE_TEXT_FIELDS:
                if field not in record.attributes:
                    continue
                value = record.attributes[field]
                text = str(value)
                if not text.strip():
                    continue
                if field == "@note" and _is_pure_tag_note(text):
                    continue
                locator = f"{idx}/{field}"
                context = "" if field == "@name" else f"数据库记录：{record_name}"
                units.append(
                    TextUnit(
                        id=compute_text_unit_id(self.engine_name, rel_path, locator),
                        engine=self.engine_name,
                        file_path=rel_path,
                        locator=locator,
                        context=context,
                        source_text=text,
                    )
                )
        return units

    def _pending_to_units(self, pending: list[_PendingUnit]) -> list[TextUnit]:
        by_group: dict[str, list[_PendingUnit]] = {}
        for p in pending:
            by_group.setdefault(p.context_group, []).append(p)

        units: list[TextUnit] = []
        for group, members in by_group.items():
            file_path = group.split(":", 1)[0]
            for p in members:
                context = "\n".join(m.source_text for m in members if m is not p)
                units.append(
                    TextUnit(
                        id=compute_text_unit_id(self.engine_name, file_path, p.locator),
                        engine=self.engine_name,
                        file_path=file_path,
                        locator=p.locator,
                        context=context,
                        source_text=p.source_text,
                    )
                )
        return units

    def inject(self, project_dir: Path, units: list[TextUnit], output_dir: Path) -> None:
        shutil.copytree(project_dir, output_dir, dirs_exist_ok=True)

        by_file: dict[str, list[TextUnit]] = {}
        for unit in units:
            by_file.setdefault(unit.file_path, []).append(unit)

        for rel_path, file_units in by_file.items():
            full_path = output_dir / rel_path
            root = read_rvdata2(full_path)
            for unit in file_units:
                value = unit.translated_text if unit.translated_text is not None else unit.source_text
                _locator_set(root, unit.locator, value)
            write_rvdata2(full_path, root)
