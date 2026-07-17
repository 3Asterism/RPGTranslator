from __future__ import annotations

import csv
from pathlib import Path

from rpg_translator.core.ir import TextUnit
from rpg_translator.core.store import Store
from rpg_translator.translate.qa import export_conflicts_csv, find_context_conflicts


def _make_unit(uid: str, source_text: str, context: str) -> TextUnit:
    return TextUnit(
        id=uid,
        engine="mz",
        file_path="data/Map001.json",
        locator=f"events/1/pages/0/list/{uid}/parameters/0",
        context=context,
        source_text=source_text,
        translated_text=f"[翻译]{source_text}",
        status="translated",
    )


def test_find_context_conflicts_flags_same_text_different_contexts(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "はい", "村長との会話"),
            _make_unit("2", "はい", "モンスターとの戦闘会話"),
        ]
        store.upsert_units(units)

        conflicts = find_context_conflicts(store)

        assert len(conflicts) == 2
        assert {c["locator"] for c in conflicts} == {
            "events/1/pages/0/list/1/parameters/0",
            "events/1/pages/0/list/2/parameters/0",
        }


def test_find_context_conflicts_ignores_same_text_same_context(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "はい", "村長との会話"),
            _make_unit("2", "はい", "村長との会話"),
        ]
        store.upsert_units(units)
        assert find_context_conflicts(store) == []


def test_find_context_conflicts_ignores_unique_source_text(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "はい", "村長との会話"),
            _make_unit("2", "いいえ", "モンスターとの戦闘会話"),
        ]
        store.upsert_units(units)
        assert find_context_conflicts(store) == []


def test_export_conflicts_csv_roundtrip(tmp_path: Path):
    conflicts = [
        {
            "source_text": "はい",
            "file_path": "data/Map001.json",
            "locator": "events/1/pages/0/list/1/parameters/0",
            "context": "村長との会話",
            "translated_text": "是",
        },
        {
            "source_text": "はい",
            "file_path": "data/Map001.json",
            "locator": "events/1/pages/0/list/2/parameters/0",
            "context": "モンスターとの戦闘会話",
            "translated_text": "是",
        },
    ]
    export_path = tmp_path / "conflicts.csv"
    export_conflicts_csv(conflicts, export_path)

    # utf-8-sig：带 BOM，方便 Windows Excel 直接正确显示中日文，不乱码
    raw = export_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")

    with open(export_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["source_text"] == "はい"
    assert rows[1]["context"] == "モンスターとの戦闘会話"
