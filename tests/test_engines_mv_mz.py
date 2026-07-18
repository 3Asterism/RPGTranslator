from __future__ import annotations

from pathlib import Path

import pytest

from rpg_translator.core.ir import TextUnit
from rpg_translator.engines.mv_mz import MVAdapter, MZAdapter


def _by_locator(units: list[TextUnit], file_path: str, locator: str) -> TextUnit:
    for u in units:
        if u.file_path == file_path and u.locator == locator:
            return u
    raise AssertionError(f"no unit at {file_path} {locator}")


# ---- detect() ----


def test_mz_detected_as_mz_only(mz_project: Path):
    assert MZAdapter.detect(mz_project) is True
    assert MVAdapter.detect(mz_project) is False


def test_mv_detected_as_mv_only(mv_project: Path):
    assert MVAdapter.detect(mv_project) is True
    assert MZAdapter.detect(mv_project) is False


def test_neither_detected_on_unrelated_dir(tmp_path: Path):
    assert MVAdapter.detect(tmp_path) is False
    assert MZAdapter.detect(tmp_path) is False


# ---- extract(): MZ ----


def test_mz_extract_finds_dialogue_and_choices(mz_project: Path):
    units = MZAdapter().extract(mz_project)
    file_path = "data/Map001.json"
    prefix = "events/1/pages/0/list"

    speaker = _by_locator(units, file_path, f"{prefix}/0/parameters/4")
    assert speaker.source_text == "ハロルド"

    line1 = _by_locator(units, file_path, f"{prefix}/1/parameters/0")
    assert line1.source_text == "こんにちは、旅人よ。"

    line2 = _by_locator(units, file_path, f"{prefix}/2/parameters/0")
    assert line2.source_text == "この村へようこそ。"

    choice0 = _by_locator(units, file_path, f"{prefix}/3/parameters/0/0")
    choice1 = _by_locator(units, file_path, f"{prefix}/3/parameters/0/1")
    assert choice0.source_text == "はい"
    assert choice1.source_text == "いいえ"


def test_mz_extract_finds_name_nickname_profile_change_commands(mz_project: Path):
    units = MZAdapter().extract(mz_project)
    file_path = "data/Map001.json"
    prefix = "events/1/pages/0/list"

    change_name = _by_locator(units, file_path, f"{prefix}/5/parameters/1")
    assert change_name.source_text == "勇者"

    change_nickname = _by_locator(units, file_path, f"{prefix}/6/parameters/1")
    assert change_nickname.source_text == "剣士"

    change_profile = _by_locator(units, file_path, f"{prefix}/7/parameters/1")
    assert change_profile.source_text == "村を守る剣士。"


def test_mz_extract_skips_comment_and_script_commands(mz_project: Path):
    units = MZAdapter().extract(mz_project)
    file_path = "data/Map001.json"
    prefix = "events/1/pages/0/list"

    locators = {u.locator for u in units if u.file_path == file_path}
    assert f"{prefix}/4/parameters/0" not in locators  # code 108 comment
    assert f"{prefix}/8/parameters/0" not in locators  # code 355 script


def test_mz_extract_skips_null_event_slots(mz_project: Path):
    units = MZAdapter().extract(mz_project)
    # events[2] is None in the fixture; nothing should be extracted from it
    assert not any(u.locator.startswith("events/2/") for u in units)


def test_mz_extract_common_events(mz_project: Path):
    units = MZAdapter().extract(mz_project)
    unit = _by_locator(units, "data/CommonEvents.json", "1/list/0/parameters/0")
    assert unit.source_text == "共通イベントのテキストです。"


def test_mz_extract_groups_sibling_dialogue_into_same_context_group(mz_project: Path):
    """不再把兄弟台词整段拼进 context（页面越长开销越是平方级）——改成给同一页面的
    条目打上相同的 context_group，交给 batch_translator 打包进同一次请求整体翻译，
    上下文靠"同一次请求里的其它行"自然获得（调研见 CLAUDE.md）。"""
    units = MZAdapter().extract(mz_project)
    line1 = _by_locator(units, "data/Map001.json", "events/1/pages/0/list/1/parameters/0")
    line2 = _by_locator(units, "data/Map001.json", "events/1/pages/0/list/2/parameters/0")
    assert line1.context == ""
    assert line1.context_group
    assert line1.context_group == line2.context_group


def test_mz_extract_database_name_nickname_and_profile(mz_project: Path):
    units = MZAdapter().extract(mz_project)
    name = _by_locator(units, "data/Actors.json", "1/name")
    assert name.source_text == "ハロルド"

    nickname = _by_locator(units, "data/Actors.json", "1/nickname")
    assert nickname.source_text == "鍛冶屋"

    profile = _by_locator(units, "data/Actors.json", "1/profile")
    assert profile.source_text == "村の鍛冶屋。"


def test_mz_extract_skips_pure_tag_note_but_keeps_mixed_note(mz_project: Path):
    units = MZAdapter().extract(mz_project)
    locators = {u.locator for u in units if u.file_path == "data/Actors.json"}

    # actor 1's note is entirely <tag> markup -> should be skipped
    assert "1/note" not in locators

    # actor 2's note mixes real text with a tag -> should be kept
    note = _by_locator(units, "data/Actors.json", "2/note")
    assert note.source_text == "実は主人公の姉。<flag:true>"


def test_mz_extract_skips_empty_fields(mz_project: Path):
    units = MZAdapter().extract(mz_project)
    locators = {(u.file_path, u.locator) for u in units}
    assert ("data/Actors.json", "1/description") not in locators
    assert ("data/Actors.json", "2/nickname") not in locators
    assert ("data/Actors.json", "2/profile") not in locators


# ---- extract(): MV differences ----


def test_mv_extract_ignores_speaker_name_param_on_101(mv_project: Path):
    units = MVAdapter().extract(mv_project)
    file_path = "www/data/Map001.json"
    locators = {u.locator for u in units if u.file_path == file_path}
    assert "events/1/pages/0/list/0/parameters/4" not in locators


def test_mv_extract_ignores_nickname_and_profile_commands_even_if_present():
    # 324/325 は MZ 専用コマンド。仮に MV 側のイベントリストに紛れ込んでいても
    # is_mz ゲートで無視されることを確認する（フィクスチャの構成に依存しない）。
    commands = [
        {"code": 324, "indent": 0, "parameters": [1, "should-not-extract-nickname"]},
        {"code": 325, "indent": 0, "parameters": [1, "should-not-extract-profile"]},
        {"code": 401, "indent": 0, "parameters": ["should-extract-this"]},
    ]
    found = MVAdapter()._extract_command_list(commands, "events/0/pages/0/list", "group")
    texts = {p.source_text for p in found}
    assert texts == {"should-extract-this"}


def test_mv_extract_still_finds_dialogue(mv_project: Path):
    units = MVAdapter().extract(mv_project)
    line1 = _by_locator(
        units, "www/data/Map001.json", "events/1/pages/0/list/1/parameters/0"
    )
    assert line1.source_text == "こんにちは、旅人よ。"


# ---- M1: extract -> inject round trip (no translation) must be byte-identical ----


def _all_json_files(root: Path) -> list[Path]:
    return sorted(p.relative_to(root) for p in root.rglob("*.json"))


def test_m1_roundtrip_untranslated_inject_is_byte_identical(tmp_path: Path, mz_project: Path):
    adapter = MZAdapter()
    units = adapter.extract(mz_project)

    output_dir = tmp_path / "output"
    adapter.inject(mz_project, units, output_dir)

    original_files = _all_json_files(mz_project)
    output_files = _all_json_files(output_dir)
    assert original_files == output_files

    for rel in original_files:
        original_bytes = (mz_project / rel).read_bytes()
        output_bytes = (output_dir / rel).read_bytes()
        assert output_bytes == original_bytes, f"{rel} differs after untranslated round trip"


def test_m1_roundtrip_mv_untranslated_inject_is_byte_identical(tmp_path: Path, mv_project: Path):
    adapter = MVAdapter()
    units = adapter.extract(mv_project)

    output_dir = tmp_path / "output"
    adapter.inject(mv_project, units, output_dir)

    for rel in _all_json_files(mv_project):
        assert (output_dir / rel).read_bytes() == (mv_project / rel).read_bytes()


def test_inject_changes_only_the_targeted_value(tmp_path: Path, mz_project: Path):
    adapter = MZAdapter()
    units = adapter.extract(mz_project)

    target = _by_locator(units, "data/Map001.json", "events/1/pages/0/list/1/parameters/0")
    target.translated_text = "TRANSLATED LINE"

    output_dir = tmp_path / "output"
    adapter.inject(mz_project, units, output_dir)

    import json

    original = json.loads((mz_project / "data/Map001.json").read_text(encoding="utf-8"))
    translated = json.loads((output_dir / "data/Map001.json").read_text(encoding="utf-8"))

    changed_path = translated["events"][1]["pages"][0]["list"][1]["parameters"][0]
    assert changed_path == "TRANSLATED LINE"

    # revert the one field we intentionally changed and confirm nothing else moved
    translated["events"][1]["pages"][0]["list"][1]["parameters"][0] = original["events"][1][
        "pages"
    ][0]["list"][1]["parameters"][0]
    assert translated == original

    # other untouched files must still be byte-identical
    for rel in ["data/System.json", "data/Actors.json", "data/CommonEvents.json"]:
        assert (output_dir / rel).read_bytes() == (mz_project / rel).read_bytes()


def test_inject_does_not_mutate_source_project(tmp_path: Path, mz_project: Path):
    adapter = MZAdapter()
    units = adapter.extract(mz_project)
    before = (mz_project / "data/Map001.json").read_bytes()

    units[0].translated_text = "whatever"
    adapter.inject(mz_project, units, tmp_path / "output")

    after = (mz_project / "data/Map001.json").read_bytes()
    assert before == after
