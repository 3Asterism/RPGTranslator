from __future__ import annotations

from pathlib import Path

import pytest

from rpg_translator.core.ir import TextUnit
from rpg_translator.core.pipeline import (
    UnknownEngineError,
    detect_adapter,
    export_mtool_json,
    export_translation_package,
    has_language_variant,
    import_translation_package,
    prune_stale_units,
    run_extract,
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


def test_prune_stale_units_removes_rows_missing_from_current_extraction(
    mz_project: Path, tmp_path: Path
):
    """text_units 表没有过期机制，同一工程反复重新提取会不断累积不再对应当前游戏
    内容的历史行（见 CLAUDE.md 相关调研）——这里模拟数据库里存在一条不属于当前
    工程任何文本的"孤儿"行（比如游戏更新后已经删掉的旧台词），验证手动清理入口
    能把它删掉，同时不影响仍然存在于游戏里的正常行。"""
    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        original_ids = {u.id for u in store.list_units()}
        stale_unit = TextUnit(
            id="stale-leftover-id",
            engine="mz",
            file_path="data/RemovedMap.json",
            locator="$.list[0]",
            context="",
            source_text="已经不存在的旧文本",
        )
        store.upsert_units([stale_unit])
        assert store.get_unit("stale-leftover-id") is not None

    deleted = prune_stale_units(mz_project, db_path)

    assert deleted == 1
    with Store(db_path) as store:
        remaining_ids = {u.id for u in store.list_units()}
    assert remaining_ids == original_ids
    assert "stale-leftover-id" not in remaining_ids


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


def test_export_mtool_json_writes_flat_source_to_translated_mapping(
    tmp_path: Path, mz_project: Path
):
    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        units = store.list_units()
        for unit in units:
            store.update_translation(unit.id, f"[译]{unit.source_text}", status="translated")

    dest_dir = tmp_path / "mtool_out"
    mtool_path, conflicts = export_mtool_json(db_path, dest_dir)

    assert mtool_path.is_file()
    assert mtool_path.name == "ManualTransFile.json"
    assert conflicts == 0

    import json

    mapping = json.loads(mtool_path.read_text(encoding="utf-8"))
    distinct_sources = {u.source_text for u in units}
    assert set(mapping.keys()) == distinct_sources
    for source_text, translated_text in mapping.items():
        assert translated_text == f"[译]{source_text}"


def test_export_mtool_json_keeps_first_translation_on_duplicate_source_text(
    tmp_path: Path, mz_project: Path
):
    """MTool 的 key 是原文本身，本项目按 locator 允许同一句原文在不同位置有不同
    译文——导出成 MTool 格式时这类冲突没法保留，只能留一个，这里验证保留的是
    第一次出现的译文，并且冲突数被如实报出来（不是静默丢数据）。"""
    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        units = store.list_units()
        assert len(units) >= 2, "fixture 至少要有两条文本才能模拟同原文不同译文"
        first, second = units[0], units[1]

        # 强制构造"同一原文、不同译文"的冲突场景：把第二个单元的 source_text 改成
        # 和第一个一样，再各自给不同译文。
        from rpg_translator.core.ir import TextUnit

        forced_second = TextUnit(
            id=second.id,
            engine=second.engine,
            file_path=second.file_path,
            locator=second.locator,
            context=second.context,
            source_text=first.source_text,
        )
        store.upsert_units([forced_second])
        store.update_translation(first.id, "译文A", status="translated")
        store.update_translation(second.id, "译文B", status="translated")
        store.commit()

    mtool_path, conflicts = export_mtool_json(db_path, tmp_path / "mtool_out")

    import json

    mapping = json.loads(mtool_path.read_text(encoding="utf-8"))
    assert conflicts == 1
    assert mapping[first.source_text] == "译文A"


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


def test_run_inject_defaults_to_in_place_no_separate_output_folder(mz_project: Path, tmp_path: Path):
    """不再传 output_dir 就应该直接原地改写 project_dir 本身，不额外生成一个
    "汉化" 目录——这是用户要求的"直接在原游戏里注入"的核心行为。"""
    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        for unit in store.list_units():
            store.update_translation(unit.id, f"[译]{unit.source_text}", status="translated")

    run_inject(mz_project, db_path)

    translated_map = (mz_project / "data" / "Map001.json").read_text(encoding="utf-8")
    assert "[译]" in translated_map
    assert has_language_variant(mz_project, "original")
    assert has_language_variant(mz_project, "translated")

    switch_language(mz_project, "original")
    original_map = (mz_project / "data" / "Map001.json").read_text(encoding="utf-8")
    assert "[译]" not in original_map
    assert "こんにちは" in original_map


def test_run_inject_in_place_preserves_true_original_across_reinject(
    mz_project: Path, tmp_path: Path
):
    """原地注入场景下 project_dir 会被 inject 直接覆盖——如果第二次注入（比如又
    翻了一批、或者改了译文重新写回）无脑重新快照"原文"，会把上一轮已经写进
    project_dir 的译文误当成原文备份下来，用户"切换为原文"就再也找不回真正的
    原文了。"""
    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        units = store.list_units()
        for unit in units:
            store.update_translation(unit.id, f"[译1]{unit.source_text}", status="translated")

    run_inject(mz_project, db_path)

    with Store(db_path) as store:
        for unit in store.list_units():
            store.update_translation(unit.id, f"[译2]{unit.source_text}", status="translated")

    run_inject(mz_project, db_path)

    translated_map = (mz_project / "data" / "Map001.json").read_text(encoding="utf-8")
    assert "[译2]" in translated_map

    switch_language(mz_project, "original")
    original_map = (mz_project / "data" / "Map001.json").read_text(encoding="utf-8")
    assert "[译1]" not in original_map
    assert "[译2]" not in original_map
    assert "こんにちは" in original_map
