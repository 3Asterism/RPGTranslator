from __future__ import annotations

from pathlib import Path

import pytest

from rpg_translator.core.ir import TextUnit
from rpg_translator.engines import wolf_binary as wb
from rpg_translator.engines.wolf import WolfAdapter
from rpg_translator.engines.wolf_binary import WolfFormatError


def _by_locator(units: list[TextUnit], file_path: str, locator: str) -> TextUnit:
    for u in units:
        if u.file_path == file_path and u.locator == locator:
            return u
    raise AssertionError(f"no unit at {file_path} {locator}")


def _locators(units: list[TextUnit], file_path: str) -> set[str]:
    return {u.locator for u in units if u.file_path == file_path}


MAP_FILE = "Data/MapData/Map001.mps"
CE_FILE = "Data/BasicData/CommonEvent.dat"
DB_FILE = "Data/BasicData/DataBase.dat"


def test_wolf_detected_via_basic_data(wolf_project: Path):
    assert WolfAdapter.detect(wolf_project) is True


def test_wolf_not_detected_on_unrelated_dir(tmp_path: Path):
    assert WolfAdapter.detect(tmp_path) is False


def test_wolf_extract_finds_map_dialogue_and_choices(wolf_project: Path):
    units = WolfAdapter().extract(wolf_project)
    prefix = "events/0/pages/0/commands"

    line1 = _by_locator(units, MAP_FILE, f"{prefix}/0/string_args/0")
    assert line1.source_text == "こんにちは、旅人よ。"

    line2 = _by_locator(units, MAP_FILE, f"{prefix}/1/string_args/0")
    assert line2.source_text == "この村へようこそ。"

    choice0 = _by_locator(units, MAP_FILE, f"{prefix}/2/string_args/0")
    choice1 = _by_locator(units, MAP_FILE, f"{prefix}/2/string_args/1")
    assert choice0.source_text == "はい"
    assert choice1.source_text == "いいえ"


def test_wolf_extract_skips_comment_command(wolf_project: Path):
    units = WolfAdapter().extract(wolf_project)
    locators = _locators(units, MAP_FILE)
    assert "events/0/pages/0/commands/3/string_args/0" not in locators  # cid 103 Comment


def test_wolf_extract_context_includes_sibling_dialogue(wolf_project: Path):
    units = WolfAdapter().extract(wolf_project)
    line1 = _by_locator(units, MAP_FILE, "events/0/pages/0/commands/0/string_args/0")
    assert "この村へようこそ。" in line1.context
    assert line1.source_text not in line1.context


def test_wolf_extract_common_events(wolf_project: Path):
    units = WolfAdapter().extract(wolf_project)
    unit = _by_locator(units, CE_FILE, "events/0/commands/0/string_args/0")
    assert unit.source_text == "共通イベントのテキストです。"
    assert "CE001" in unit.context

    locators = _locators(units, CE_FILE)
    assert "events/0/commands/1/string_args/0" not in locators  # cid 103 Comment


def test_wolf_extract_database_text_and_skip_rules(wolf_project: Path):
    units = WolfAdapter().extract(wolf_project)

    name0 = _by_locator(units, DB_FILE, "types/0/data/0/string_values/0")
    assert name0.source_text == "ハロルド"
    desc0 = _by_locator(units, DB_FILE, "types/0/data/0/string_values/1")
    assert desc0.source_text == "村の鍛冶屋。"
    assert "Actors" in desc0.context

    name1 = _by_locator(units, DB_FILE, "types/0/data/1/string_values/0")
    assert name1.source_text == "アリス"

    locators = _locators(units, DB_FILE)
    # record 1's description is empty -> not extracted
    assert "types/0/data/1/string_values/1" not in locators
    # record 2's name is fine but its description contains a newline -> not extracted
    assert "types/0/data/2/string_values/0" in locators
    assert "types/0/data/2/string_values/1" not in locators
    # the "参照名" field (type == 1, not a plain text field) is never extracted,
    # for any record, regardless of content
    assert "types/0/data/0/string_values/2" not in locators
    assert "types/0/data/1/string_values/2" not in locators
    assert "types/0/data/2/string_values/2" not in locators
    all_texts = {u.source_text for u in units}
    assert "normal" not in all_texts
    assert "hidden" not in all_texts


def _all_wolf_files(root: Path) -> list[Path]:
    patterns = ("*.mps", "*.dat", "*.project")
    found: set[Path] = set()
    for pattern in patterns:
        found.update(p.relative_to(root) for p in root.rglob(pattern))
    return sorted(found)


def test_wolf_roundtrip_untranslated_inject_is_byte_identical(tmp_path: Path, wolf_project: Path):
    adapter = WolfAdapter()
    units = adapter.extract(wolf_project)

    output_dir = tmp_path / "output"
    adapter.inject(wolf_project, units, output_dir)

    original_files = _all_wolf_files(wolf_project)
    output_files = _all_wolf_files(output_dir)
    assert original_files == output_files
    assert original_files  # sanity: fixture actually produced files

    for rel in original_files:
        original_bytes = (wolf_project / rel).read_bytes()
        output_bytes = (output_dir / rel).read_bytes()
        assert output_bytes == original_bytes, f"{rel} differs after untranslated round trip"


def test_wolf_inject_changes_only_the_targeted_value(tmp_path: Path, wolf_project: Path):
    adapter = WolfAdapter()
    units = adapter.extract(wolf_project)

    target = _by_locator(units, MAP_FILE, "events/0/pages/0/commands/0/string_args/0")
    target.translated_text = "TRANSLATED LINE"

    output_dir = tmp_path / "output"
    adapter.inject(wolf_project, units, output_dir)

    translated_map = wb.WolfMap.read(output_dir / MAP_FILE)
    changed = translated_map.events[0].pages[0].commands[0]
    assert changed.string_args[0] == "TRANSLATED LINE"

    # other lines in the same map, and other files entirely, must be untouched
    assert translated_map.events[0].pages[0].commands[1].string_args[0] == "この村へようこそ。"
    for rel in [CE_FILE, DB_FILE, "Data/BasicData/DataBase.project"]:
        assert (output_dir / rel).read_bytes() == (wolf_project / rel).read_bytes()


def test_wolf_inject_does_not_mutate_source_project(tmp_path: Path, wolf_project: Path):
    adapter = WolfAdapter()
    units = adapter.extract(wolf_project)
    before = (wolf_project / MAP_FILE).read_bytes()

    units[0].translated_text = "whatever"
    adapter.inject(wolf_project, units, tmp_path / "output")

    after = (wolf_project / MAP_FILE).read_bytes()
    assert before == after


# ---------------------------------------------------------------------------
# wolf_binary-level tests: encoding detection and the "loud failure on
# encrypted files" requirement (spec section 14 / M4.8 acceptance note --
# detect() must not silently produce an empty result on data it can't read).
# ---------------------------------------------------------------------------


def test_wolf_binary_database_utf8_roundtrip(tmp_path: Path):
    field_name = wb.Field(name="Name", type=0, index_info=wb._FIELD_STRING_START + 0)
    record = wb.DataRecord(name="0", int_values=[], string_values=["Hello, world -- ユニコード"])
    db_type = wb.DbType(
        name="Items", fields=[field_name], data=[record], description="", field_type_list_size=1
    )
    db = wb.WolfDatabase(types=[db_type], is_utf8=True)

    project_path = tmp_path / "Items.project"
    dat_path = tmp_path / "Items.dat"
    db.write(project_path, dat_path)

    # The UTF-8 marker byte must literally be 0x55 ('U') in the raw file.
    raw = dat_path.read_bytes()
    assert raw[1 + wb._DAT_UTF8_INDEX] == 0x55

    reloaded = wb.WolfDatabase.read(project_path, dat_path)
    assert reloaded.is_utf8 is True
    assert reloaded.types[0].data[0].string_values[0] == "Hello, world -- ユニコード"


def test_wolf_binary_common_event_without_optional_tail_roundtrips(tmp_path: Path):
    """CommonEvent's unknown10/unknown12 are only present when the trailing
    indicator byte is 0x92 instead of 0x91 -- exercise the simpler (no
    unknown10) branch, which the main fixture doesn't cover."""
    event = wb.CommonEvent(
        event_id=0,
        unknown1=0,
        unknown2=bytes(7),
        name="Simple",
        commands=[],
        unknown11="",
        description="",
        unknown3=[""] * 10,
        unknown4=[0] * 10,
        unknown5=[[] for _ in range(10)],
        unknown6=[[] for _ in range(10)],
        unknown7=bytes(0x1D),
        unknown8=[""] * 100,
        unknown9="",
        unknown10=None,
        unknown12=None,
    )
    ce = wb.WolfCommonEvents(events=[event])
    path = tmp_path / "CommonEvent.dat"
    ce.write(path)

    reloaded = wb.WolfCommonEvents.read(path)
    assert len(reloaded.events) == 1
    assert reloaded.events[0].unknown10 is None
    assert reloaded.events[0].unknown12 is None
    assert reloaded.events[0].name == "Simple"


def test_wolf_database_read_raises_clear_error_on_encrypted_file(tmp_path: Path):
    project_path = tmp_path / "Fake.project"
    dat_path = tmp_path / "Fake.dat"
    project_path.write_bytes((0).to_bytes(4, "little"))
    # first byte != 0 signals "encrypted" -- must raise, not silently parse garbage
    dat_path.write_bytes(bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]))

    with pytest.raises(WolfFormatError):
        wb.WolfDatabase.read(project_path, dat_path)


def test_wolf_common_events_read_raises_clear_error_on_encrypted_file(tmp_path: Path):
    path = tmp_path / "CommonEvent.dat"
    path.write_bytes(bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]))

    with pytest.raises(WolfFormatError):
        wb.WolfCommonEvents.read(path)


def test_wolf_map_read_raises_clear_error_on_garbage(tmp_path: Path):
    path = tmp_path / "Map999.mps"
    path.write_bytes(b"not a wolf map file at all")

    with pytest.raises(WolfFormatError):
        wb.WolfMap.read(path)
