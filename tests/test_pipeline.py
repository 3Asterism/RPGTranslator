from __future__ import annotations

from pathlib import Path

import pytest

from rpg_translator.core.pipeline import (
    UnknownEngineError,
    detect_adapter,
    export_translation_package,
    has_language_variant,
    import_translation_package,
    run_extract,
    run_glossary,
    run_inject,
    switch_language,
)
from rpg_translator.core.store import Store


def test_detect_adapter_picks_mz(mz_project: Path):
    adapter = detect_adapter(mz_project)
    assert adapter.engine_name == "mz"


def test_detect_adapter_picks_mv(mv_project: Path):
    adapter = detect_adapter(mv_project)
    assert adapter.engine_name == "mv"


def test_detect_adapter_picks_vxace(vxace_project: Path):
    adapter = detect_adapter(vxace_project)
    assert adapter.engine_name == "vxace"


def test_detect_adapter_raises_on_unrecognized_dir(tmp_path: Path):
    with pytest.raises(UnknownEngineError):
        detect_adapter(tmp_path)


@pytest.mark.anyio
async def test_run_glossary_reuses_existing_glossary_without_calling_api(tmp_path: Path):
    """断点续传场景：术语表已经抽取过、存在 db 里了，重新点"开始翻译"不应该
    再花一次 token 重新抽一遍——api_key=None 也不该报错，因为根本不需要真的调用。"""
    db_path = tmp_path / "units.db"
    with Store(db_path) as store:
        store.set_glossary({"ハロルド": "哈罗德"})

    result = await run_glossary(db_path, api_key=None, base_url="unused", model="unused")
    assert result == {"ハロルド": "哈罗德"}


@pytest.mark.anyio
async def test_run_glossary_force_still_requires_api_key(tmp_path: Path):
    from rpg_translator.core.pipeline import MissingApiKeyError

    db_path = tmp_path / "units.db"
    with Store(db_path) as store:
        store.set_glossary({"ハロルド": "哈罗德"})

    with pytest.raises(MissingApiKeyError):
        await run_glossary(db_path, api_key=None, base_url="unused", model="unused", force=True)


def test_export_and_import_translation_package_round_trip(tmp_path: Path, mz_project: Path):
    """导出的翻译包在另一份（这里用同一个 fixture 模拟"同版本游戏"）全新 db 上
    导入，应该精确套上已翻译的内容——这是"分享给拿到同一个游戏的人"这个功能的
    核心契约：id 靠 engine+file_path+locator 算出来，同版本游戏两边天然一致。"""
    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        units = store.list_units()
        for unit in units:
            store.update_translation(unit.id, f"[译]{unit.source_text}", status="translated")
        store.set_glossary({"ハロルド": "哈罗德"})

    dest_dir = tmp_path / "share"
    package_path = export_translation_package(db_path, "TestGame", dest_dir)
    assert package_path.is_file()
    assert package_path.name == "TestGame.rpgtrans.json"

    # 模拟"另一个人"：全新的 db，先跑一遍 extract（同一份工程，id 天然对得上）
    other_db_path = tmp_path / "other_units.db"
    run_extract(mz_project, other_db_path)

    imported, skipped = import_translation_package(other_db_path, package_path)
    assert skipped == 0
    assert imported == len(units)

    with Store(other_db_path) as store:
        assert store.get_glossary() == {"ハロルド": "哈罗德"}
        for unit in store.list_units():
            assert unit.status == "translated"
            assert unit.translated_text == f"[译]{unit.source_text}"


def test_import_translation_package_skips_units_with_changed_source_text(
    tmp_path: Path, mz_project: Path
):
    """分享者那边的游戏版本和本地不一致（同一个 id 但原文变了），必须跳过，
    不能把可能已经过时/对不上号的译文硬套进去。"""
    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        unit = store.list_units()[0]
        store.update_translation(unit.id, "译文", status="translated")

    package_path = export_translation_package(db_path, "TestGame", tmp_path / "share")

    other_db_path = tmp_path / "other_units.db"
    with Store(other_db_path) as store:
        from rpg_translator.core.ir import TextUnit

        store.upsert_units(
            [TextUnit(id=unit.id, engine=unit.engine, file_path=unit.file_path, locator=unit.locator,
                      context="", source_text="原文已经改了")]
        )

    imported, skipped = import_translation_package(other_db_path, package_path)
    assert imported == 0
    assert skipped == 1


def test_switch_language_toggles_output_dir_between_original_and_translated(
    tmp_path: Path, mz_project: Path
):
    db_path = tmp_path / "units.db"
    output_dir = tmp_path / "output"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        for unit in store.list_units():
            store.update_translation(unit.id, f"[译]{unit.source_text}", status="translated")

    run_inject(mz_project, db_path, output_dir)

    assert has_language_variant(output_dir, "original")
    assert has_language_variant(output_dir, "translated")

    translated_map = (output_dir / "data" / "Map001.json").read_text(encoding="utf-8")
    assert "[译]" in translated_map

    switch_language(output_dir, "original")
    original_map = (output_dir / "data" / "Map001.json").read_text(encoding="utf-8")
    assert "[译]" not in original_map
    assert "こんにちは" in original_map

    switch_language(output_dir, "translated")
    back_to_translated = (output_dir / "data" / "Map001.json").read_text(encoding="utf-8")
    assert "[译]" in back_to_translated


def test_switch_language_raises_clear_error_when_no_backup_exists(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        switch_language(tmp_path / "no_such_output", "original")
