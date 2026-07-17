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


# ---------------------------------------------------------------------------
# "v3.5" 格式（LZ4 压缩 + Page features/page_transfer + Command 尾部数据块）
# —— M4.9 用真实 WOLF RPG Editor v3.712 自带示例工程实测后补上的分支，见
# wolf_binary.py 模块文档"真实工程验证"一节。真实工程没有提交进仓库，这里
# 用 conftest.py 的 build_wolf_project_v35() 手搭 fixture 做回归测试。
# ---------------------------------------------------------------------------

V35_MAP_FILE = "Data/MapData/Map001.mps"
V35_CE_FILE = "Data/BasicData/CommonEvent.dat"
V35_DB_FILE = "Data/BasicData/DataBase.dat"


def test_wolf_v35_extract_finds_dialogue_across_all_three_file_types(wolf_project_v35: Path):
    units = WolfAdapter().extract(wolf_project_v35)

    map_unit = _by_locator(units, V35_MAP_FILE, "events/0/pages/0/commands/0/string_args/0")
    assert map_unit.source_text == "こんにちは、v3.5！"

    ce_unit = _by_locator(units, V35_CE_FILE, "events/0/commands/0/string_args/0")
    assert ce_unit.source_text == "v3.5 共通イベントのテキスト。"

    db_unit = _by_locator(units, V35_DB_FILE, "types/0/data/0/string_values/0")
    assert db_unit.source_text == "ハロルド"


def test_wolf_v35_map_read_write_is_lossless(wolf_project_v35: Path):
    """LZ4 重新压缩不保证复现原始压缩字节（见模块文档"真实工程验证"），
    所以这里比较解压后的结构化内容，而不是原始文件字节。"""
    original = wb.WolfMap.read(wolf_project_v35 / V35_MAP_FILE)

    out_path = wolf_project_v35.parent / "Map001_rewritten.mps"
    original.write(out_path)
    reloaded = wb.WolfMap.read(out_path)

    assert reloaded.version == original.version == 0x67
    assert reloaded.is_utf8 is True
    assert reloaded.layer_cnt == 3
    assert reloaded.tiles == original.tiles
    page = reloaded.events[0].pages[0]
    original_page = original.events[0].pages[0]
    assert page.features == original_page.features == 5
    assert page.page_transfer == original_page.page_transfer == 7
    assert [c.string_args for c in page.commands] == [c.string_args for c in original_page.commands]
    assert page.commands[0].v35_unknown == bytes([9, 9, 9])


def test_wolf_v35_page_transfer_absent_when_features_not_greater_than_3():
    """features <= 3 时 page_transfer 字段不存在于文件里，写回时也不能写出
    这个字段——否则真实引擎按老格式读这份文件会多读一个字节，后续全部错位。"""
    page_low = wb.Page(
        unknown1=0,
        graphic_name="",
        graphic_direction=0,
        graphic_frame=0,
        graphic_opacity=255,
        graphic_render_mode=0,
        conditions=bytes(wb._CONDITIONS_SIZE),
        movement=bytes(wb._MOVEMENT_SIZE),
        flags=0,
        route_flags=0,
        route=[],
        commands=[],
        shadow_graphic_num=0,
        collision_width=0,
        collision_height=0,
        features=3,
        page_transfer=99,  # 应该被忽略/不写出，因为 features 没有 > 3
    )
    w = wb.ByteWriter()
    page_low.write(w)
    r = wb.ByteReader(w.getvalue())
    reloaded = wb.Page.read(r)
    assert reloaded.features == 3
    assert reloaded.page_transfer == 0
    assert r.eof()


def test_wolf_v35_common_event_lz4_roundtrip_and_v35_unknown_preserved(wolf_project_v35: Path):
    original = wb.WolfCommonEvents.read(wolf_project_v35 / V35_CE_FILE)
    assert original.version == 0x93
    assert original.v35 is True

    out_path = wolf_project_v35.parent / "CommonEvent_rewritten.dat"
    original.write(out_path)
    reloaded = wb.WolfCommonEvents.read(out_path)

    assert reloaded.events[0].commands[0].string_args == ["v3.5 共通イベントのテキスト。"]
    assert reloaded.events[0].commands[0].v35_unknown == bytes([1, 2])


def test_wolf_v35_database_lz4_roundtrip(wolf_project_v35: Path):
    project_path = wolf_project_v35 / "Data/BasicData/DataBase.project"
    dat_path = wolf_project_v35 / V35_DB_FILE
    original = wb.WolfDatabase.read(project_path, dat_path)
    assert original.version == 0xC4

    out_dir = wolf_project_v35.parent / "db_rewritten"
    out_dir.mkdir()
    original.write(out_dir / "DataBase.project", out_dir / "DataBase.dat")
    reloaded = wb.WolfDatabase.read(out_dir / "DataBase.project", out_dir / "DataBase.dat")

    assert reloaded.types[0].data[0].string_values == ["ハロルド"]


def test_wolf_v35_database_type_unknown2_sentinel_roundtrips():
    """WolfTL 独有、wolftrans 没有的分支：unknown1 命中哨兵值时多一个字符串
    字段。真实工程里没见过命中，但结构必须留着这个位置。"""
    field_name = wb.Field(name="名前", type=0, index_info=wb._FIELD_STRING_START + 0)
    dbtype = wb.DbType(
        name="Weird",
        fields=[field_name],
        data=[wb.DataRecord(name="0", int_values=[], string_values=["x"])],
        description="",
        field_type_list_size=1,
        unknown1=wb._DAT_STRING_INDICATOR,
        unknown2="哨兵字符串",
    )
    w = wb.ByteWriter(encoding=wb.UTF8)
    dbtype.write_dat(w)
    r = wb.ByteReader(w.getvalue(), encoding=wb.UTF8)
    reloaded = wb.DbType(
        name="Weird", fields=[wb.Field(name="名前")], data=[wb.DataRecord(name="0")], description="",
        field_type_list_size=1,
    )
    reloaded.read_dat(r)
    assert reloaded.unknown1 == wb._DAT_STRING_INDICATOR
    assert reloaded.unknown2 == "哨兵字符串"
    assert reloaded.data[0].string_values == ["x"]
    assert r.eof()


def test_wolf_v35_inject_changes_only_targeted_value(tmp_path: Path, wolf_project_v35: Path):
    adapter = WolfAdapter()
    units = adapter.extract(wolf_project_v35)

    target = _by_locator(units, V35_MAP_FILE, "events/0/pages/0/commands/0/string_args/0")
    target.translated_text = "V35 TRANSLATED"

    output_dir = tmp_path / "output"
    adapter.inject(wolf_project_v35, units, output_dir)

    translated_map = wb.WolfMap.read(output_dir / V35_MAP_FILE)
    assert translated_map.events[0].pages[0].commands[0].string_args[0] == "V35 TRANSLATED"
    assert translated_map.events[0].pages[0].commands[1].string_args[0] == "二行目のテキスト。"
    # 压缩格式写回不保证逐字节等于原文件（见模块文档），但内容要能正常
    # 再解析，且未改动的另外两个文件解压内容应保持不变
    reloaded_ce = wb.WolfCommonEvents.read(output_dir / V35_CE_FILE)
    original_ce = wb.WolfCommonEvents.read(wolf_project_v35 / V35_CE_FILE)
    assert reloaded_ce.events[0].commands[0].string_args == original_ce.events[0].commands[0].string_args
