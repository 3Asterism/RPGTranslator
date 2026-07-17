from __future__ import annotations

from pathlib import Path

import pytest

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.gui.glossary_dialog import GlossaryDialog
from rpg_translator.gui.main_window import MainWindow, resolve_dropped_path
from rpg_translator.gui.settings_dialog import SettingsDialog
from rpg_translator.gui.workers import ExtractAndGlossaryWorker, TranslateAndInjectWorker


def test_main_window_constructs_with_start_disabled(qapp):
    window = MainWindow()
    assert window._start_button.isEnabled() is False
    assert window._adapter is None


def test_drop_recognized_mz_project_enables_start_and_shows_engine(qapp, mz_project: Path):
    window = MainWindow()
    window._on_path_dropped(mz_project)

    assert window._start_button.isEnabled() is True
    assert window._adapter is not None
    assert window._adapter.engine_name == "mz"
    assert "RPG Maker MZ" in window._info_label.text()
    assert "14" in window._info_label.text()  # mz_project fixture 固定能扫出 14 条


def test_drop_unrecognized_dir_keeps_start_disabled(qapp, tmp_path: Path):
    not_a_game = tmp_path / "not_a_game"
    not_a_game.mkdir()

    window = MainWindow()
    window._on_path_dropped(not_a_game)

    assert window._start_button.isEnabled() is False
    assert window._adapter is None
    assert "未识别到支持的 RPG Maker 引擎" in window._info_label.text()


def test_resolve_dropped_path_returns_parent_dir_for_a_file(tmp_path: Path):
    game_exe = tmp_path / "Game.exe"
    game_exe.write_bytes(b"")
    assert resolve_dropped_path(game_exe) == tmp_path


def test_resolve_dropped_path_returns_folder_unchanged(tmp_path: Path):
    project_dir = tmp_path / "game_project"
    project_dir.mkdir()
    assert resolve_dropped_path(project_dir) == project_dir


def test_settings_dialog_persists_model_concurrency_output_dir(qapp):
    dialog = SettingsDialog()
    dialog._model_combo.setCurrentText("deepseek-v4-pro")
    dialog._concurrency_spin.setValue(16)
    dialog._output_dir_edit.setText("my_output")
    dialog._api_key_edit.setText("test-key-not-real")

    dialog._on_accept()

    reloaded = SettingsDialog()
    assert reloaded.model == "deepseek-v4-pro"
    assert reloaded.concurrency == 16
    assert reloaded.output_dir == "my_output"
    assert reloaded._api_key_edit.text() == "test-key-not-real"


def test_glossary_dialog_edited_glossary_reflects_table_edits(qapp, tmp_path: Path):
    dialog = GlossaryDialog(tmp_path / "units.db", {"ハロルド": "哈罗德"})
    assert dialog.edited_glossary() == {"ハロルド": "哈罗德"}

    dialog._table.item(0, 1).setText("哈洛德")
    assert dialog.edited_glossary() == {"ハロルド": "哈洛德"}


def test_glossary_dialog_accept_saves_to_store(qapp, tmp_path: Path):
    from rpg_translator.core.store import Store

    db_path = tmp_path / "units.db"
    dialog = GlossaryDialog(db_path, {"ハロルド": "哈罗德"})
    dialog._on_accept()

    with Store(db_path) as store:
        assert store.get_glossary() == {"ハロルド": "哈罗德"}


def test_extract_and_glossary_worker_end_to_end(qapp, tmp_path: Path, mz_project: Path):
    """真实跑一遍 ExtractAndGlossaryWorker 这个 QThread：验证跨线程 Signal 真的能
    把 (candidates, unit_count) 传回主线程，而不是只测同步的 pipeline 函数本身。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        pytest.skip("本地未配置 DEEPSEEK_API_KEY，跳过真实 API 调用测试")

    settings = Settings()
    db_path = tmp_path / "units.db"

    results: list[tuple[dict, int]] = []
    errors: list[str] = []

    worker = ExtractAndGlossaryWorker(
        mz_project, db_path, api_key, settings.deepseek_base_url, settings.deepseek_model
    )
    worker.finished_ok.connect(lambda candidates, count: results.append((candidates, count)))
    worker.failed.connect(errors.append)

    worker.start()
    worker.wait(60_000)
    qapp.processEvents()

    assert errors == []
    assert len(results) == 1
    candidates, unit_count = results[0]
    assert unit_count == 14
    assert isinstance(candidates, dict)
    assert db_path.is_file()


def test_translate_and_inject_worker_end_to_end(qapp, tmp_path: Path, mz_project: Path):
    """真实跑一遍 TranslateAndInjectWorker：extract 先同步跑完占位，再驱动这个
    worker 做 translate -> inject，检查 stage/progress/finished 三个 Signal 都触发了。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        pytest.skip("本地未配置 DEEPSEEK_API_KEY，跳过真实 API 调用测试")

    from rpg_translator.core.pipeline import run_extract

    settings = Settings()
    db_path = tmp_path / "units.db"
    output_dir = tmp_path / "output"
    run_extract(mz_project, db_path)

    stages: list[str] = []
    progress_updates: list[tuple[int, int]] = []
    results: list[tuple[int, str]] = []
    errors: list[str] = []

    worker = TranslateAndInjectWorker(
        mz_project,
        db_path,
        output_dir,
        api_key,
        settings.deepseek_base_url,
        settings.deepseek_model,
        concurrency=4,
    )
    worker.stage_changed.connect(stages.append)
    worker.progress_changed.connect(lambda done, total: progress_updates.append((done, total)))
    worker.finished_ok.connect(lambda count, out: results.append((count, out)))
    worker.failed.connect(errors.append)

    worker.start()
    worker.wait(120_000)
    qapp.processEvents()

    assert errors == []
    assert len(results) == 1
    unit_count, out_dir = results[0]
    assert unit_count == 14
    assert out_dir == str(output_dir)
    assert "翻译中…" in stages
    assert "写回中…" in stages
    assert len(progress_updates) > 0
    assert (output_dir / "data" / "System.json").is_file()
