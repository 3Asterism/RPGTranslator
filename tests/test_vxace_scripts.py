from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from rpg_translator.engines._vxace_scripts import (
    ScriptsFormatError,
    _MARSHAL_HEADER,
    _TAG_ARRAY,
    _write_long,
    append_script,
    encode_new_entry,
    has_conflicting_message_system,
    read_scripts,
)


def _build_scripts_file(path: Path, entries: list[tuple[int, str, str]]) -> None:
    """entries: (id, name, source) 三元组列表，source 会被 zlib 压缩后写入，
    和真实 Scripts.rvdata2 的存储方式一致（见 encode_new_entry）。"""
    body = _MARSHAL_HEADER + bytes([_TAG_ARRAY]) + _write_long(len(entries))
    for script_id, name, source in entries:
        body += encode_new_entry(script_id, name, source)
    path.write_bytes(body)


def test_read_scripts_round_trips_names_and_decompressed_source(tmp_path: Path):
    path = tmp_path / "Scripts.rvdata2"
    _build_scripts_file(
        path,
        [
            (1, "Vocab", "module Vocab\nend\n"),
            (2, "Window_Message", "class Window_Message\nend\n"),
        ],
    )

    entries = read_scripts(path)
    assert [e.name for e in entries] == ["Vocab", "Window_Message"]
    assert [e.id for e in entries] == [1, 2]
    assert zlib.decompress(entries[1].compressed_source) == b"class Window_Message\nend\n"


def test_read_scripts_empty_array_is_fine(tmp_path: Path):
    path = tmp_path / "Scripts.rvdata2"
    _build_scripts_file(path, [])
    assert read_scripts(path) == []


def test_read_scripts_rejects_missing_marshal_header(tmp_path: Path):
    path = tmp_path / "Scripts.rvdata2"
    path.write_bytes(b"not a marshal file at all")
    with pytest.raises(ScriptsFormatError):
        read_scripts(path)


def test_append_script_preserves_existing_entries_byte_for_byte(tmp_path: Path):
    path = tmp_path / "Scripts.rvdata2"
    _build_scripts_file(
        path,
        [(1, "Vocab", "module Vocab\nend\n"), (2, "Sound", "module Sound\nend\n")],
    )
    before = read_scripts(path)

    append_script(path, 999, "RPGTranslator_RuntimeLineWrap", "puts 1\n")

    after = read_scripts(path)
    assert len(after) == 3
    assert [(e.id, e.name, e.compressed_source) for e in after[:2]] == [
        (e.id, e.name, e.compressed_source) for e in before
    ]
    assert after[2].id == 999
    assert after[2].name == "RPGTranslator_RuntimeLineWrap"
    assert zlib.decompress(after[2].compressed_source) == b"puts 1\n"


def test_has_conflicting_message_system_detects_known_keywords():
    class FakeEntry:
        def __init__(self, name: str):
            self.name = name

    assert has_conflicting_message_system([FakeEntry("Vocab"), FakeEntry("YEA-MessageSystem")]) == (
        "YEA-MessageSystem"
    )
    assert has_conflicting_message_system([FakeEntry("Galv_MessageBusts")]) is not None
    assert has_conflicting_message_system([FakeEntry("Vocab"), FakeEntry("Window_Message")]) is None


def test_encode_new_entry_round_trips_non_ascii_name_and_source(tmp_path: Path):
    path = tmp_path / "Scripts.rvdata2"
    _build_scripts_file(path, [(1, "テスト", "puts 'なし'\n")])
    entries = read_scripts(path)
    assert entries[0].name == "テスト"
    assert zlib.decompress(entries[0].compressed_source).decode("utf-8") == "puts 'なし'\n"
