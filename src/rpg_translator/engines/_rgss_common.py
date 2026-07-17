"""VX Ace / XP / VX（RGSS1〜3）共通のヘルパー。どれも Ruby Marshal + `code`/`parameters`
形式のイベントコマンドという同じ骨格を共有しているため、ここにまとめている。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, ClassVar

from rubymarshal.classes import RubyObject

from rpg_translator.codec.rvdata2_codec import read_rvdata2, write_rvdata2
from rpg_translator.core.ir import EngineName, TextUnit, compute_text_unit_id
from rpg_translator.engines.base import EngineAdapter

_PURE_TAG_NOTE_RE = re.compile(r"^(\s*<[^<>\r\n]+>\s*)+$")


def is_pure_tag_note(text: str) -> bool:
    return bool(_PURE_TAG_NOTE_RE.match(text))


def rv_get(obj: Any, key: Any) -> Any:
    if isinstance(obj, RubyObject):
        return obj.attributes[key]
    return obj[key]


def rv_set(obj: Any, key: Any, value: Any) -> None:
    if isinstance(obj, RubyObject):
        obj.attributes[key] = value
    else:
        obj[key] = value


def parse_locator(locator: str) -> list[Any]:
    segments: list[Any] = []
    for seg in locator.split("/"):
        if seg.startswith("@"):
            segments.append(seg)
        elif seg.lstrip("-").isdigit():
            segments.append(int(seg))
        else:
            segments.append(seg)
    return segments


def locator_get(root: Any, locator: str) -> Any:
    cur = root
    for seg in parse_locator(locator):
        cur = rv_get(cur, seg)
    return cur


def locator_set(root: Any, locator: str, value: Any) -> None:
    segments = parse_locator(locator)
    cur = root
    for seg in segments[:-1]:
        cur = rv_get(cur, seg)
    rv_set(cur, segments[-1], value)


class PendingUnit:
    __slots__ = ("locator", "source_text", "context_group")

    def __init__(self, locator: str, source_text: str, context_group: str):
        self.locator = locator
        self.source_text = source_text
        self.context_group = context_group


def extract_command_list(
    commands: list[RubyObject], path_prefix: str, group: str
) -> list[PendingUnit]:
    found: list[PendingUnit] = []
    for i, cmd in enumerate(commands):
        code = cmd.attributes.get("@code")
        params = cmd.attributes.get("@parameters", [])
        if code in (401, 405):
            if params and str(params[0]):
                found.append(PendingUnit(f"{path_prefix}/{i}/@parameters/0", str(params[0]), group))
        elif code == 102:
            choices = params[0] if params else []
            for ci, choice in enumerate(choices):
                if str(choice):
                    found.append(
                        PendingUnit(f"{path_prefix}/{i}/@parameters/0/{ci}", str(choice), group)
                    )
        elif code == 320:
            if len(params) > 1 and str(params[1]):
                found.append(PendingUnit(f"{path_prefix}/{i}/@parameters/1", str(params[1]), group))
        # code 101 はヘッダーのみで話者名パラメータなし（MZ 独自機能）
        # 108/408 (Comment)・355/655 (Script) はデフォルトで無視（MV/MZ と同じ方針）
    return found


DATABASE_TEXT_FIELDS = ["@name", "@nickname", "@description", "@note", "@message1", "@message2"]
MAP_FILE_RE = re.compile(r"^Map\d{3}\.")


class RGSSAdapterBase(EngineAdapter):
    """VX Ace / XP / VX 共通の extract/inject 実装。サブクラスは engine_name・data_dir・
    file_extension・database_files・detect() だけ定義すればいい。"""

    engine_name: ClassVar[EngineName]
    data_dir: ClassVar[str] = "Data"
    file_extension: ClassVar[str]
    database_files: ClassVar[list[str]]

    def extract(self, project_dir: Path) -> list[TextUnit]:
        data_root = project_dir / self.data_dir
        pending: list[PendingUnit] = []

        for map_file in sorted(data_root.glob(f"Map*{self.file_extension}")):
            if not MAP_FILE_RE.match(map_file.name):
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
                        extract_command_list(page.attributes.get("@list", []), path_prefix, group)
                    )

        common_events_file = data_root / f"CommonEvents{self.file_extension}"
        if common_events_file.is_file():
            rel_path = f"{self.data_dir}/CommonEvents{self.file_extension}"
            common_events = read_rvdata2(common_events_file)
            for ce_idx, ce in enumerate(common_events):
                if ce is None:
                    continue
                group = f"{rel_path}:{ce_idx}"
                path_prefix = f"{ce_idx}/@list"
                pending.extend(
                    extract_command_list(ce.attributes.get("@list", []), path_prefix, group)
                )

        units = self._pending_to_units(pending)

        for db_filename in self.database_files:
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
            for field in DATABASE_TEXT_FIELDS:
                if field not in record.attributes:
                    continue
                text = str(record.attributes[field])
                if not text.strip():
                    continue
                if field == "@note" and is_pure_tag_note(text):
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

    def _pending_to_units(self, pending: list[PendingUnit]) -> list[TextUnit]:
        by_group: dict[str, list[PendingUnit]] = {}
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
                locator_set(root, unit.locator, value)
            write_rvdata2(full_path, root)
