"""WOLF RPG Editor (ウディタ) adapter.

See wolf_binary.py's module docstring for the full research/feasibility
write-up (provenance of the format knowledge, what is verified vs. not, and
the explicit list of what this module does NOT attempt to handle). Short
version of the M4.8 go/no-go call: GO for unencrypted WOLF projects using the
"classic"/older on-disk layout (which is the expected shape of a developer's
own editable project directory -- WolfPro protection is an opt-in
publish-time feature); encrypted or otherwise-unrecognized files raise a
clear WolfFormatError instead of silently producing empty/garbled output.

Directory layout targeted (confirmed via WolfTL's own loader code, not just
the project spec's secondhand section 6.4):
    Data/BasicData/Game.dat                (not parsed -- see wolf_binary.py gap 3)
    Data/BasicData/CommonEvent.dat         (WolfCommonEvents)
    Data/BasicData/<Name>.project + <Name>.dat   (WolfDatabase, one pair per
                                                   database "type table";
                                                   SysDataBaseBasic.project is
                                                   skipped -- it has no
                                                   matching .dat and holds no
                                                   translatable data)
    Data/MapData/**/*.mps                  (WolfMap, searched recursively
                                             under Data/ like the reference
                                             tools do, not just MapData/)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from rpg_translator.core.ir import TextUnit, compute_text_unit_id
from rpg_translator.engines.base import EngineAdapter
from rpg_translator.engines.wolf_binary import (
    Command,
    WolfCommonEvents,
    WolfDatabase,
    WolfMap,
    iter_command_texts,
    locator_get,
    locator_set,
    translatable_fields,
)

_ENGINE = "wolf"
_SKIPPED_PROJECT_FILES = {"sysdatabasebasic.project"}


def _relative(path: Path, project_dir: Path) -> str:
    return path.relative_to(project_dir).as_posix()


def _texts_with_sibling_context(
    commands: list[Command], locator_prefix: str
) -> list[tuple[str, str, str]]:
    """(locator, text, context) triples, where context is every other
    translatable line in the same command list joined with newlines --
    mirrors _rgss_common.py's/mv_mz.py's "sibling dialogue" context
    convention (same Show-Text-message-window grouping idea, just derived
    from WOLF's own command list instead of an RPG Maker page's @list)."""
    entries = [(loc, text) for loc, text in iter_command_texts(commands, locator_prefix) if text.strip()]
    result: list[tuple[str, str, str]] = []
    for loc, text in entries:
        context = "\n".join(t for other_loc, t in entries if other_loc != loc)
        result.append((loc, text, context))
    return result


class WolfAdapter(EngineAdapter):
    @staticmethod
    def detect(project_dir: Path) -> bool:
        basic_data = project_dir / "Data" / "BasicData"
        return (basic_data / "Game.dat").is_file() or (basic_data / "CommonEvent.dat").is_file()

    def extract(self, project_dir: Path) -> list[TextUnit]:
        units: list[TextUnit] = []
        units.extend(self._extract_maps(project_dir))
        units.extend(self._extract_common_events(project_dir))
        units.extend(self._extract_databases(project_dir))
        return units

    def _extract_maps(self, project_dir: Path) -> list[TextUnit]:
        units: list[TextUnit] = []
        data_dir = project_dir / "Data"
        if not data_dir.is_dir():
            return units
        for map_path in sorted(data_dir.rglob("*.mps")):
            rel_path = _relative(map_path, project_dir)
            game_map = WolfMap.read(map_path)
            for event_idx, event in enumerate(game_map.events):
                for page_idx, page in enumerate(event.pages):
                    prefix = f"events/{event_idx}/pages/{page_idx}/commands"
                    for locator, text, context in _texts_with_sibling_context(page.commands, prefix):
                        units.append(
                            TextUnit(
                                id=compute_text_unit_id(_ENGINE, rel_path, locator),
                                engine=_ENGINE,
                                file_path=rel_path,
                                locator=locator,
                                context=context,
                                source_text=text,
                            )
                        )
        return units

    def _extract_common_events(self, project_dir: Path) -> list[TextUnit]:
        units: list[TextUnit] = []
        ce_path = project_dir / "Data" / "BasicData" / "CommonEvent.dat"
        if not ce_path.is_file():
            return units
        rel_path = _relative(ce_path, project_dir)
        common_events = WolfCommonEvents.read(ce_path)
        for event_idx, event in enumerate(common_events.events):
            prefix = f"events/{event_idx}/commands"
            for locator, text, context in _texts_with_sibling_context(event.commands, prefix):
                full_context = f"通用事件：{event.name}" + (f"\n{context}" if context else "")
                units.append(
                    TextUnit(
                        id=compute_text_unit_id(_ENGINE, rel_path, locator),
                        engine=_ENGINE,
                        file_path=rel_path,
                        locator=locator,
                        context=full_context,
                        source_text=text,
                    )
                )
        return units

    def _extract_databases(self, project_dir: Path) -> list[TextUnit]:
        units: list[TextUnit] = []
        basic_data = project_dir / "Data" / "BasicData"
        if not basic_data.is_dir():
            return units
        for project_file in sorted(basic_data.glob("*.project")):
            if project_file.name.lower() in _SKIPPED_PROJECT_FILES:
                continue
            dat_file = project_file.with_suffix(".dat")
            if not dat_file.is_file():
                continue
            rel_path = _relative(dat_file, project_dir)
            db = WolfDatabase.read(project_file, dat_file)
            for type_idx, db_type in enumerate(db.types):
                fields = translatable_fields(db_type)
                for data_idx, record in enumerate(db_type.data):
                    for f in fields:
                        idx = f.value_index()
                        if idx >= len(record.string_values):
                            continue
                        text = record.string_values[idx]
                        if not text or "\n" in text:
                            continue
                        locator = f"types/{type_idx}/data/{data_idx}/string_values/{idx}"
                        units.append(
                            TextUnit(
                                id=compute_text_unit_id(_ENGINE, rel_path, locator),
                                engine=_ENGINE,
                                file_path=rel_path,
                                locator=locator,
                                context=f"数据库：{db_type.name}/{record.name}",
                                source_text=text,
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
            if full_path.suffix == ".mps":
                game_map = WolfMap.read(full_path)
                for unit in file_units:
                    locator_set(game_map, unit.locator, _resolved_text(unit))
                game_map.write(full_path)
            elif full_path.name == "CommonEvent.dat":
                common_events = WolfCommonEvents.read(full_path)
                for unit in file_units:
                    locator_set(common_events, unit.locator, _resolved_text(unit))
                common_events.write(full_path)
            elif full_path.suffix == ".dat":
                project_path = full_path.with_suffix(".project")
                db = WolfDatabase.read(project_path, full_path)
                for unit in file_units:
                    locator_set(db, unit.locator, _resolved_text(unit))
                db.write(project_path, full_path)
            # 其他文件类型（Game.dat 等）本引擎不解析，原样保留 copytree 出来的内容即可


def _resolved_text(unit: TextUnit) -> str:
    return unit.translated_text if unit.translated_text is not None else unit.source_text


# locator_get is re-exported for tests/debugging convenience even though the
# adapter itself only needs locator_set.
__all__ = ["WolfAdapter", "locator_get"]
