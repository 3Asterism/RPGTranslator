from pathlib import Path

from rpg_translator.core.ir import TextUnit, compute_source_hash
from rpg_translator.core.store import Store


def _make_unit(unit_id: str, engine: str = "mv", source_text: str = "こんにちは") -> TextUnit:
    return TextUnit(
        id=unit_id,
        engine=engine,
        file_path="www/data/Map001.json",
        locator=f"$.list[{unit_id}]",
        context="前文",
        source_text=source_text,
        control_code_map={"⟦CC0⟧": "\\C[1]"},
    )


def test_upsert_and_get_roundtrips_all_fields(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("u1")
        store.upsert_units([unit])

        fetched = store.get_unit("u1")
        assert fetched == unit


def test_upsert_is_idempotent_update(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        store.upsert_units([_make_unit("u1", source_text="原文")])
        store.upsert_units([_make_unit("u1", source_text="修改后的原文")])

        fetched = store.get_unit("u1")
        assert fetched is not None
        assert fetched.source_text == "修改后的原文"
        assert len(store.list_units()) == 1


def test_get_unit_missing_returns_none(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        assert store.get_unit("does-not-exist") is None


def test_list_units_filters_by_engine_and_status(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        store.upsert_units(
            [
                _make_unit("mv1", engine="mv"),
                _make_unit("mz1", engine="mz"),
            ]
        )
        store.update_translation("mv1", "こんにちは(訳)", status="translated")

        mv_units = store.list_units(engine="mv")
        assert [u.id for u in mv_units] == ["mv1"]

        translated_units = store.list_units(status="translated")
        assert [u.id for u in translated_units] == ["mv1"]
        assert translated_units[0].translated_text == "こんにちは(訳)"

        pending_units = store.list_units(status="pending")
        assert [u.id for u in pending_units] == ["mz1"]


def test_translation_memory_roundtrip(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        source_text = "こんにちは"
        source_hash = compute_source_hash(source_text)

        assert store.get_memory(source_hash) is None

        store.set_memory(source_hash, source_text, "你好")
        assert store.get_memory(source_hash) == "你好"

        store.set_memory(source_hash, source_text, "你好呀")
        assert store.get_memory(source_hash) == "你好呀"


def test_glossary_roundtrip_and_update(tmp_path: Path):
    with Store(tmp_path / "units.db") as store:
        assert store.get_glossary() == {}

        store.set_glossary({"ハロルド": "哈罗德", "村": "村庄"})
        assert store.get_glossary() == {"ハロルド": "哈罗德", "村": "村庄"}

        store.set_glossary({"ハロルド": "哈洛德"})
        assert store.get_glossary() == {"ハロルド": "哈洛德", "村": "村庄"}


def test_data_persists_across_store_reopen(tmp_path: Path):
    db_path = tmp_path / "units.db"
    with Store(db_path) as store:
        store.upsert_units([_make_unit("u1")])

    with Store(db_path) as reopened:
        assert reopened.get_unit("u1") is not None
