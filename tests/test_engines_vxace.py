from __future__ import annotations

from pathlib import Path

from rpg_translator.core.ir import TextUnit
from rpg_translator.engines.vxace import VXAceAdapter


def _by_locator(units: list[TextUnit], file_path: str, locator: str) -> TextUnit:
    for u in units:
        if u.file_path == file_path and u.locator == locator:
            return u
    raise AssertionError(f"no unit at {file_path} {locator}")


def test_vxace_detected_via_actors_rvdata2(vxace_project: Path):
    assert VXAceAdapter.detect(vxace_project) is True


def test_vxace_not_detected_on_unrelated_dir(tmp_path: Path):
    assert VXAceAdapter.detect(tmp_path) is False


def test_vxace_extract_finds_dialogue_and_choices(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    file_path = "Data/Map001.rvdata2"
    prefix = "@events/1/@pages/0/@list"

    line1 = _by_locator(units, file_path, f"{prefix}/0/@parameters/0")
    assert line1.source_text == "こんにちは、旅人よ。"

    line2 = _by_locator(units, file_path, f"{prefix}/1/@parameters/0")
    assert line2.source_text == "この村へようこそ。"

    choice0 = _by_locator(units, file_path, f"{prefix}/2/@parameters/0/0")
    choice1 = _by_locator(units, file_path, f"{prefix}/2/@parameters/0/1")
    assert choice0.source_text == "はい"
    assert choice1.source_text == "いいえ"


def test_vxace_extract_finds_change_name_command(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    unit = _by_locator(
        units, "Data/Map001.rvdata2", "@events/1/@pages/0/@list/4/@parameters/1"
    )
    assert unit.source_text == "勇者"


def test_vxace_extract_skips_comment_and_script_commands(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    locators = {u.locator for u in units if u.file_path == "Data/Map001.rvdata2"}
    prefix = "@events/1/@pages/0/@list"
    assert f"{prefix}/3/@parameters/0" not in locators  # code 108 comment
    assert f"{prefix}/5/@parameters/0" not in locators  # code 355 script


def test_vxace_extract_common_events(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    unit = _by_locator(units, "Data/CommonEvents.rvdata2", "1/@list/0/@parameters/0")
    assert unit.source_text == "共通イベントのテキストです。"


def test_vxace_extract_database_name_nickname_description(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    name = _by_locator(units, "Data/Actors.rvdata2", "1/@name")
    assert name.source_text == "ハロルド"

    nickname = _by_locator(units, "Data/Actors.rvdata2", "1/@nickname")
    assert nickname.source_text == "鍛冶屋"

    description = _by_locator(units, "Data/Actors.rvdata2", "1/@description")
    assert description.source_text == "村の鍛冶屋。"


def test_vxace_extract_skips_pure_tag_note_but_keeps_mixed_note(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    locators = {u.locator for u in units if u.file_path == "Data/Actors.rvdata2"}

    assert "1/@note" not in locators  # actor 1's note は完全に <tag> だけ

    note = _by_locator(units, "Data/Actors.rvdata2", "2/@note")
    assert note.source_text == "実は主人公の姉。<flag:true>"


def test_vxace_extract_skips_empty_fields(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    locators = {(u.file_path, u.locator) for u in units}
    assert ("Data/Actors.rvdata2", "2/@nickname") not in locators
    assert ("Data/Actors.rvdata2", "2/@description") not in locators


def test_vxace_extract_context_includes_sibling_dialogue(vxace_project: Path):
    units = VXAceAdapter().extract(vxace_project)
    line1 = _by_locator(
        units, "Data/Map001.rvdata2", "@events/1/@pages/0/@list/0/@parameters/0"
    )
    assert "この村へようこそ。" in line1.context
    assert line1.source_text not in line1.context


def _all_rvdata2_files(root: Path) -> list[Path]:
    return sorted(p.relative_to(root) for p in root.rglob("*.rvdata2"))


def test_m4_roundtrip_untranslated_inject_is_byte_identical(tmp_path: Path, vxace_project: Path):
    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    original_files = _all_rvdata2_files(vxace_project)
    output_files = _all_rvdata2_files(output_dir)
    assert original_files == output_files

    for rel in original_files:
        original_bytes = (vxace_project / rel).read_bytes()
        output_bytes = (output_dir / rel).read_bytes()
        assert output_bytes == original_bytes, f"{rel} differs after untranslated round trip"


def test_vxace_inject_changes_only_the_targeted_value(tmp_path: Path, vxace_project: Path):
    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)

    target = _by_locator(
        units, "Data/Map001.rvdata2", "@events/1/@pages/0/@list/0/@parameters/0"
    )
    target.translated_text = "TRANSLATED LINE"

    output_dir = tmp_path / "output"
    adapter.inject(vxace_project, units, output_dir)

    from rpg_translator.codec.rvdata2_codec import read_rvdata2

    translated_map = read_rvdata2(output_dir / "Data" / "Map001.rvdata2")
    changed = translated_map.attributes["@events"][1].attributes["@pages"][0].attributes["@list"][0]
    assert str(changed.attributes["@parameters"][0]) == "TRANSLATED LINE"

    # 其他文件必须原样不动
    for rel in ["Data/CommonEvents.rvdata2", "Data/Actors.rvdata2"]:
        assert (output_dir / rel).read_bytes() == (vxace_project / rel).read_bytes()


def test_vxace_inject_does_not_mutate_source_project(tmp_path: Path, vxace_project: Path):
    adapter = VXAceAdapter()
    units = adapter.extract(vxace_project)
    before = (vxace_project / "Data" / "Map001.rvdata2").read_bytes()

    units[0].translated_text = "whatever"
    adapter.inject(vxace_project, units, tmp_path / "output")

    after = (vxace_project / "Data" / "Map001.rvdata2").read_bytes()
    assert before == after
