from __future__ import annotations

from pathlib import Path

import pytest

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.gui.main_window import (
    MainWindow,
    db_path_for_project,
    default_output_dir,
    resolve_dropped_path,
)
from rpg_translator.gui.settings_dialog import SettingsDialog
from rpg_translator.gui.workers import ExtractWorker, InjectWorker, TranslateWorker


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
    assert window._output_dir_edit.text() == str(default_output_dir(mz_project))


def test_default_output_dir_is_sibling_of_project_dir_not_cwd_relative(tmp_path: Path):
    project_dir = tmp_path / "MyGame"
    assert default_output_dir(project_dir) == tmp_path / "MyGame_汉化"


def test_db_path_for_project_points_at_rpg_translator_units_db(tmp_path: Path):
    assert db_path_for_project(tmp_path) == tmp_path / ".rpg_translator" / "units.db"


def test_drop_recognized_vxace_project_enables_start_and_shows_engine(qapp, vxace_project: Path):
    window = MainWindow()
    window._on_path_dropped(vxace_project)

    assert window._start_button.isEnabled() is True
    assert window._adapter is not None
    assert window._adapter.engine_name == "vxace"
    assert "RPG Maker VX Ace" in window._info_label.text()
    # M5 起同一条 Show Text 的连续行合并成一个 TextUnit（spec 第 9 节），fixture 原本
    # 能扫出的 11 条里有 2 行合并成 1 条，固定扫出 10 条
    assert "10" in window._info_label.text()


def test_drop_unrecognized_dir_keeps_start_disabled(qapp, tmp_path: Path):
    not_a_game = tmp_path / "not_a_game"
    not_a_game.mkdir()

    window = MainWindow()
    window._on_path_dropped(not_a_game)

    assert window._start_button.isEnabled() is False
    assert window._adapter is None
    assert "未识别到支持的 RPG Maker 引擎" in window._info_label.text()


def test_load_translated_project_without_db_warns_and_leaves_inject_disabled(
    qapp, tmp_path: Path, monkeypatch
):
    # QMessageBox.warning() 是真的模态对话框，headless 测试环境里没有人去点掉它，
    # 不 monkeypatch 掉的话这个测试会直接卡死（Qt 事件循环里空等一个永远不会来的
    # 用户点击，CPU 占用趋近于 0，看起来像挂起但其实是在等一个不存在的人）。
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "rpg_translator.gui.main_window.QMessageBox.warning",
        lambda self, title, text: warnings.append((title, text)),
    )

    window = MainWindow()
    window._load_translated_project(tmp_path)

    assert window._inject_button.isEnabled() is False
    assert len(warnings) == 1
    assert "未找到翻译记录" in warnings[0][0]


def test_load_translated_project_with_existing_db_enables_inject(qapp, tmp_path: Path):
    from rpg_translator.core.ir import TextUnit
    from rpg_translator.core.store import Store

    db_path = db_path_for_project(tmp_path)
    db_path.parent.mkdir(parents=True)
    with Store(db_path) as store:
        unit = TextUnit(
            id="u1",
            engine="mv",
            file_path="www/data/Map001.json",
            locator="0/list/0/parameters/0",
            context="",
            source_text="こんにちは",
            translated_text="你好",
            status="translated",
        )
        store.upsert_units([unit])

    window = MainWindow()
    window._load_translated_project(tmp_path)

    assert window._inject_button.isEnabled() is True
    assert window._project_dir == tmp_path
    assert window._db_path == db_path
    assert "1 条已翻译" in window._info_label.text()
    assert window._output_dir_edit.text() == str(default_output_dir(tmp_path))


def test_drop_project_with_existing_progress_shows_resume_note(qapp, mz_project: Path):
    """之前翻译到一半、软件重开后再拖入同一个工程，应该能看到已完成的进度提示，
    而不是看起来像是"从零开始"——断点续传这件事本身要在界面上可见。"""
    from rpg_translator.core.pipeline import run_extract
    from rpg_translator.core.store import Store

    db_path = db_path_for_project(mz_project)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        first_unit = store.list_units()[0]
        store.update_translation(first_unit.id, "已翻译的内容", status="translated")

    window = MainWindow()
    window._on_path_dropped(mz_project)

    assert "已翻译 1/14" in window._info_label.text()


def test_drop_project_without_prior_progress_shows_no_resume_note(qapp, mz_project: Path):
    window = MainWindow()
    window._on_path_dropped(mz_project)

    assert "已翻译" not in window._info_label.text()


def test_stop_button_hidden_until_translation_starts_and_calls_worker_stop(qapp):
    window = MainWindow()
    assert window._stop_button.isVisible() is False

    stopped = []
    window._translate_worker = type("_FakeWorker", (), {"stop": lambda self: stopped.append(True)})()
    window._stop_button.setVisible(True)
    window._stop_button.setEnabled(True)

    window._on_stop_clicked()

    assert stopped == [True]
    assert window._stop_button.isEnabled() is False


def test_export_translation_package_prompts_for_name_and_writes_file(
    qapp, tmp_path: Path, mz_project: Path, monkeypatch
):
    from rpg_translator.core.pipeline import run_extract
    from rpg_translator.core.store import Store

    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)
    with Store(db_path) as store:
        unit = store.list_units()[0]
        store.update_translation(unit.id, "译文", status="translated")

    window = MainWindow()
    window._project_dir = mz_project
    window._db_path = db_path

    dest_dir = tmp_path / "share_dest"
    dest_dir.mkdir()
    monkeypatch.setattr(
        "rpg_translator.gui.main_window.QInputDialog.getText",
        lambda *a, **k: ("我的游戏", True),
    )
    monkeypatch.setattr(
        "rpg_translator.gui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(dest_dir),
    )
    monkeypatch.setattr("rpg_translator.gui.main_window.QMessageBox.information", lambda *a, **k: None)

    window._on_export_package_clicked()

    assert (dest_dir / "我的游戏.rpgtrans.json").is_file()


def test_import_translation_package_button_imports_and_enables_inject(
    qapp, tmp_path: Path, mz_project: Path, monkeypatch
):
    from rpg_translator.core.pipeline import export_translation_package, run_extract
    from rpg_translator.core.store import Store

    source_db = tmp_path / "source_units.db"
    run_extract(mz_project, source_db)
    with Store(source_db) as store:
        for unit in store.list_units():
            store.update_translation(unit.id, f"[译]{unit.source_text}", status="translated")
    package_path = export_translation_package(source_db, "TestGame", tmp_path / "share")

    window = MainWindow()
    window._project_dir = mz_project
    window._db_path = None

    monkeypatch.setattr(
        "rpg_translator.gui.main_window.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(package_path), ""),
    )
    monkeypatch.setattr("rpg_translator.gui.main_window.QMessageBox.information", lambda *a, **k: None)

    window._on_import_package_clicked()

    assert window._inject_button.isEnabled() is True
    with Store(window._db_path) as store:
        translated = store.list_units(status="translated")
        assert len(translated) == 14


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


def test_settings_dialog_persists_base_url_and_fallback_provider(qapp):
    """GUI 里能直接填 base_url 和备用 provider（不用改 .env）——用户没有真的
    DeepSeek Key，日常靠 SiliconFlow/DashScope 之类的兼容服务当主/备用 provider，
    这几个字段必须能在界面上配、还能在重开设置对话框后读回来。"""
    dialog = SettingsDialog()
    dialog._base_url_edit.setText("https://api.siliconflow.cn/v1")
    dialog._fallback_api_key_edit.setText("fallback-key-not-real")
    dialog._fallback_base_url_edit.setText("https://dashscope.aliyuncs.com/compatible-mode/v1")
    dialog._fallback_model_edit.setText("qwen-plus")

    dialog._on_accept()

    reloaded = SettingsDialog()
    assert reloaded.base_url == "https://api.siliconflow.cn/v1"
    assert reloaded._fallback_api_key_edit.text() == "fallback-key-not-real"
    assert reloaded.fallback_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert reloaded.fallback_model == "qwen-plus"


def test_settings_dialog_base_url_falls_back_to_env_default_when_unset(qapp):
    from rpg_translator.config import Settings

    dialog = SettingsDialog()
    assert dialog.base_url == Settings().deepseek_base_url


def test_extract_worker_end_to_end(qapp, tmp_path: Path, mz_project: Path):
    """真实跑一遍 ExtractWorker 这个 QThread：验证跨线程 Signal 真的能把 unit_count
    传回主线程，而不是只测同步的 pipeline 函数本身。纯本地操作，不需要 API Key。"""
    db_path = tmp_path / "units.db"

    results: list[int] = []
    errors: list[str] = []

    worker = ExtractWorker(mz_project, db_path)
    worker.finished_ok.connect(results.append)
    worker.failed.connect(errors.append)

    worker.start()
    finished_in_time = worker.wait(10_000)
    qapp.processEvents()

    assert finished_in_time, "worker 线程 10s 内没跑完（线程卡住了）"
    assert errors == []
    assert results == [14]
    assert db_path.is_file()


def test_translate_worker_end_to_end(qapp, tmp_path: Path, mz_project: Path):
    """真实跑一遍 TranslateWorker：extract 先同步跑完占位，再驱动这个 worker 做
    translate，检查 stage/progress/finished 三个 Signal 都触发了，且不碰游戏工程本身
    （translate 和 inject 拆开跑，这里只验证 translate 这一半）。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        pytest.skip("本地未配置 DEEPSEEK_API_KEY，跳过真实 API 调用测试")

    from rpg_translator.core.pipeline import run_extract

    settings = Settings()
    db_path = tmp_path / "units.db"
    run_extract(mz_project, db_path)

    stages: list[str] = []
    progress_updates: list[tuple[int, int]] = []
    results: list[int] = []
    failures: list[list[tuple[str, str]]] = []
    errors: list[str] = []

    worker = TranslateWorker(
        db_path,
        api_key,
        settings.deepseek_base_url,
        settings.deepseek_model,
        concurrency=4,
    )
    worker.stage_changed.connect(stages.append)
    worker.progress_changed.connect(lambda done, total: progress_updates.append((done, total)))
    worker.finished_ok.connect(lambda count, failed: (results.append(count), failures.append(failed)))
    worker.failed.connect(errors.append)

    worker.start()
    finished_in_time = worker.wait(180_000)
    qapp.processEvents()

    assert finished_in_time, "worker 线程 180s 内没跑完（真实 API 调用异常慢，或线程卡住了）"
    assert errors == []
    assert results == [14]
    assert failures == [[]]
    assert "翻译中…" in stages
    assert len(progress_updates) > 0


def test_inject_worker_writes_translated_units_without_needing_api_key(
    qapp, tmp_path: Path, mz_project: Path
):
    """InjectWorker 不需要 API Key——纯粹是把 db 里已经翻译好的内容写回工程，
    所以用手工构造的已翻译 Store 就能测，不用打真实 API 调用。"""
    from rpg_translator.core.pipeline import run_extract
    from rpg_translator.core.store import Store

    db_path = tmp_path / "units.db"
    output_dir = tmp_path / "output"
    run_extract(mz_project, db_path)

    with Store(db_path) as store:
        for unit in store.list_units():
            store.update_translation(unit.id, f"[译]{unit.source_text}", status="translated")

    results: list[tuple[int, str]] = []
    errors: list[str] = []

    worker = InjectWorker(mz_project, db_path, output_dir)
    worker.finished_ok.connect(lambda count, out: results.append((count, out)))
    worker.failed.connect(errors.append)

    worker.start()
    finished_in_time = worker.wait(10_000)
    qapp.processEvents()

    assert finished_in_time
    assert errors == []
    assert results == [(14, str(output_dir))]
    assert (output_dir / "data" / "System.json").is_file()
