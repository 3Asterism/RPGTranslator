from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar

from rpg_translator.core.ir import EngineName, TextUnit, compute_text_unit_id
from rpg_translator.engines.base import EngineAdapter, copy_project_if_different

_DATABASE_FILES = [
    "Actors.json",
    "Classes.json",
    "Skills.json",
    "Items.json",
    "Weapons.json",
    "Armors.json",
    "Enemies.json",
    "States.json",
    "Troops.json",
]
_DATABASE_TEXT_FIELDS = [
    "name",
    "nickname",
    "description",
    "profile",
    "note",
    "message1",
    "message2",
    "message3",
    "message4",
]
_MAP_FILE_RE = re.compile(r"^Map\d{3}\.json$")
_PURE_TAG_NOTE_RE = re.compile(r"^(\s*<[^<>\r\n]+>\s*)+$")

_JSON_DUMP_KWARGS: dict[str, Any] = {"ensure_ascii": False, "separators": (",", ":")}


def _parse_locator(locator: str) -> list[str | int]:
    return [int(seg) if seg.isdigit() else seg for seg in locator.split("/")]


def _json_path_get(obj: Any, path: list[str | int]) -> Any:
    cur = obj
    for seg in path:
        cur = cur[seg]
    return cur


def _json_path_set(obj: Any, path: list[str | int], value: Any) -> None:
    cur = obj
    for seg in path[:-1]:
        cur = cur[seg]
    cur[path[-1]] = value


def _is_pure_tag_note(text: str) -> bool:
    return bool(_PURE_TAG_NOTE_RE.match(text))


class _PendingUnit:
    __slots__ = ("locator", "source_text", "context_group")

    def __init__(self, locator: str, source_text: str, context_group: str):
        self.locator = locator
        self.source_text = source_text
        self.context_group = context_group


class _MVMZAdapterBase(EngineAdapter):
    engine_name: ClassVar[EngineName]
    data_dir: ClassVar[str]
    is_mz: ClassVar[bool]

    def extract(self, project_dir: Path) -> list[TextUnit]:
        data_root = project_dir / self.data_dir
        pending: list[_PendingUnit] = []

        for map_file in sorted(data_root.glob("Map*.json")):
            if not _MAP_FILE_RE.match(map_file.name):
                continue
            rel_path = f"{self.data_dir}/{map_file.name}"
            data = json.loads(map_file.read_text(encoding="utf-8"))
            for event_idx, event in enumerate(data.get("events", [])):
                if not event:
                    continue
                for page_idx, page in enumerate(event.get("pages", [])):
                    group = f"{rel_path}:events/{event_idx}/pages/{page_idx}"
                    path_prefix = f"events/{event_idx}/pages/{page_idx}/list"
                    pending.extend(
                        self._extract_command_list(page.get("list", []), path_prefix, group)
                    )

        common_events_file = data_root / "CommonEvents.json"
        if common_events_file.is_file():
            rel_path = f"{self.data_dir}/CommonEvents.json"
            data = json.loads(common_events_file.read_text(encoding="utf-8"))
            for ce_idx, ce in enumerate(data):
                if not ce:
                    continue
                group = f"{rel_path}:{ce_idx}"
                path_prefix = f"{ce_idx}/list"
                pending.extend(self._extract_command_list(ce.get("list", []), path_prefix, group))

        units = self._pending_to_units(pending)

        for db_filename in _DATABASE_FILES:
            db_file = data_root / db_filename
            if not db_file.is_file():
                continue
            rel_path = f"{self.data_dir}/{db_filename}"
            data = json.loads(db_file.read_text(encoding="utf-8"))
            units.extend(self._extract_database_file(data, rel_path))

        return units

    def _extract_command_list(
        self, commands: list[dict[str, Any]], path_prefix: str, group: str
    ) -> list[_PendingUnit]:
        found: list[_PendingUnit] = []
        for i, cmd in enumerate(commands):
            code = cmd.get("code")
            params = cmd.get("parameters", [])
            if code == 101:
                if self.is_mz and len(params) > 4 and isinstance(params[4], str) and params[4].strip():
                    found.append(_PendingUnit(f"{path_prefix}/{i}/parameters/4", params[4], group))
            elif code in (401, 405):
                if params and isinstance(params[0], str) and params[0]:
                    found.append(_PendingUnit(f"{path_prefix}/{i}/parameters/0", params[0], group))
            elif code == 102:
                choices = params[0] if params else []
                for ci, choice in enumerate(choices):
                    if isinstance(choice, str) and choice:
                        found.append(
                            _PendingUnit(f"{path_prefix}/{i}/parameters/0/{ci}", choice, group)
                        )
            elif code == 320:
                if len(params) > 1 and isinstance(params[1], str) and params[1]:
                    found.append(_PendingUnit(f"{path_prefix}/{i}/parameters/1", params[1], group))
            elif code in (324, 325) and self.is_mz:
                if len(params) > 1 and isinstance(params[1], str) and params[1]:
                    found.append(_PendingUnit(f"{path_prefix}/{i}/parameters/1", params[1], group))
            # 108/408 (Comment) 和 355/655 (Script) 默认跳过；code 0 是终止符，同样跳过
        return found

    def _extract_database_file(self, records: list[Any], rel_path: str) -> list[TextUnit]:
        units: list[TextUnit] = []
        for idx, record in enumerate(records):
            if not record:
                continue
            record_name = record.get("name", "") if isinstance(record, dict) else ""
            for field in _DATABASE_TEXT_FIELDS:
                if field not in record:
                    continue
                text = record[field]
                if not isinstance(text, str) or not text.strip():
                    continue
                if field == "note" and _is_pure_tag_note(text):
                    continue
                locator = f"{idx}/{field}"
                context = "" if field == "name" else f"数据库记录：{record_name}"
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
        # 不再把同页其它台词整段拼进 context（页面越长开销越是平方级）——改成只带一个
        # 分组 id，交给 batch_translator 把同一分组的台词打包进同一次请求整体翻译，
        # 上下文靠"同一次请求里的其它行"自然获得（调研见 CLAUDE.md）。
        units: list[TextUnit] = []
        for p in pending:
            file_path = p.context_group.split(":", 1)[0]
            units.append(
                TextUnit(
                    id=compute_text_unit_id(self.engine_name, file_path, p.locator),
                    engine=self.engine_name,
                    file_path=file_path,
                    locator=p.locator,
                    context="",
                    context_group=p.context_group,
                    source_text=p.source_text,
                )
            )
        return units

    def inject(self, project_dir: Path, units: list[TextUnit], output_dir: Path) -> None:
        copy_project_if_different(project_dir, output_dir)

        by_file: dict[str, list[TextUnit]] = {}
        for unit in units:
            by_file.setdefault(unit.file_path, []).append(unit)

        for rel_path, file_units in by_file.items():
            full_path = output_dir / rel_path
            data = json.loads(full_path.read_text(encoding="utf-8"))
            for unit in file_units:
                path_segments = _parse_locator(unit.locator)
                value = unit.translated_text if unit.translated_text is not None else unit.source_text
                _json_path_set(data, path_segments, value)
            full_path.write_text(json.dumps(data, **_JSON_DUMP_KWARGS), encoding="utf-8")


class MVAdapter(_MVMZAdapterBase):
    engine_name: ClassVar[EngineName] = "mv"
    data_dir: ClassVar[str] = "www/data"
    is_mz: ClassVar[bool] = False

    @staticmethod
    def detect(project_dir: Path) -> bool:
        return (project_dir / "www" / "data" / "System.json").is_file()


class MZAdapter(_MVMZAdapterBase):
    engine_name: ClassVar[EngineName] = "mz"
    data_dir: ClassVar[str] = "data"
    is_mz: ClassVar[bool] = True

    @staticmethod
    def detect(project_dir: Path) -> bool:
        return (project_dir / "data" / "System.json").is_file() and not (
            project_dir / "www"
        ).is_dir()
