from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.gui.main_window import (
    MainWindow,
    _format_duration,
    db_path_for_project,
    default_output_dir,
    resolve_dropped_path,
)
from rpg_translator.gui.settings_dialog import ENGINE_LOCAL, ENGINE_ONLINE, SettingsDialog
from rpg_translator.gui.workers import ExtractWorker, InjectWorker, TranslateWorker


def _mock_transport(status_code: int = 200) -> httpx.MockTransport:
    """假的 httpx transport，不碰真实网络——注入 SettingsDialog._connectivity_transport，
    用来在测试里模拟"服务端有响应"（不管 2xx 还是 401，只要收到响应就算连通）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    return httpx.MockTransport(handler)


def _unreachable_transport() -> httpx.MockTransport:
    """模拟连接失败（DNS/连接拒绝这类传输层错误），不是收到了错误状态码的响应。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handler)


def test_main_window_constructs_with_start_disabled(qapp):
    window = MainWindow()
    assert window._start_button.isEnabled() is False
    assert window._adapter is None


def test_format_duration_formats_seconds_minutes_hours():
    assert _format_duration(30) == "30 秒"
    assert _format_duration(90) == "2 分钟"
    assert _format_duration(7200) == "2 小时"
    assert _format_duration(4860) == "1 小时 21 分钟"  # 1.35 小时该拆成"时+分"而不是一个小数
    assert _format_duration(-5) == "0 秒"  # 时钟/取整误差导致的负数不应该显示成负的


def test_main_window_compute_eta_text_before_enough_samples_shows_placeholder(qapp):
    """样本不够（还没攒够至少 2 个进度点）或者一条都没翻完时，不硬算一个不靠谱的速度。"""
    window = MainWindow()
    assert window._compute_eta_text(0, 100, time.monotonic()) == "翻译速度：统计中…"

    window._progress_samples.append((time.monotonic(), 1))
    assert window._compute_eta_text(1, 100, time.monotonic()) == "翻译速度：统计中…"


def test_main_window_compute_eta_text_computes_speed_and_remaining(qapp):
    window = MainWindow()
    window._progress_samples.append((0.0, 0))
    window._progress_samples.append((10.0, 5))  # 10 秒完成 5 批 -> 0.5 批/秒 -> 30 批/分钟

    text = window._compute_eta_text(5, 100, 10.0)

    assert "30.0 批/分钟" in text
    assert "还剩 95 批" in text
    # 剩余 95 批 / 0.5 批/秒 = 190 秒 ≈ 3 分钟
    assert "3 分钟" in text


def test_main_window_compute_eta_text_all_done_has_no_remaining_text(qapp):
    window = MainWindow()
    window._progress_samples.append((0.0, 0))
    window._progress_samples.append((10.0, 100))

    text = window._compute_eta_text(100, 100, 10.0)

    assert "批/分钟" in text
    assert "还剩" not in text


def test_main_window_on_progress_changed_updates_eta_label(qapp):
    window = MainWindow()
    window._on_progress_changed(0, 10)
    assert window._eta_label.text() == "翻译速度：统计中…"
    assert len(window._progress_samples) == 1


def test_main_window_on_progress_changed_bursts_do_not_inflate_sample_count(qapp):
    """按事件页面分组打包后，一次请求解析完会在同一个同步循环里背靠背连续 emit
    几十个 on_progress（见 batch_translator._translate_batch）——这些调用之间真实
    耗时几乎为 0。如果每次都存一个样本，deque(maxlen=20) 会被这一个瞬间的爆发
    全部填满，"最老样本到现在"的时间差趋近于 0，算出来的速度会离谱地飙到几千/上万
    批每分钟（实测复现过 11004.4 批/分钟）。取样本身要按最小时间间隔节流，同一次
    爆发只应该落进 1 个样本点。"""
    window = MainWindow()
    for i in range(1, 51):
        window._on_progress_changed(i, 100)

    assert len(window._progress_samples) <= 2


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
    dialog._connectivity_transport = _mock_transport()

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
    dialog._api_key_edit.setText("test-key-not-real")
    dialog._connectivity_transport = _mock_transport()

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


def test_settings_dialog_selecting_online_engine_shows_online_box(qapp):
    # 注意：用 isHidden() 不用 isVisible()——对话框本身没 show() 过，子控件的
    # isVisible() 恒为 False（依赖祖先是否可见），isHidden() 才反映 setVisible()
    # 设置的"显式隐藏"标记，不受父窗口有没有 show() 影响。
    #
    # QSettings 在这个测试套件里是跨测试共享的同一个临时 ini 文件（见 conftest.py），
    # 不会在每个测试前自动清空，所以这里显式选中"在线"而不是依赖"默认就是在线"——
    # 不然如果先跑了别的把引擎切成本地的测试，这里会因为残留状态而失败。
    dialog = SettingsDialog()
    online_index = dialog._engine_combo.findData(ENGINE_ONLINE)
    dialog._engine_combo.setCurrentIndex(online_index)
    assert not dialog._online_box.isHidden()
    assert dialog._local_box.isHidden()

    dialog._api_key_edit.setText("test-key-not-real")
    dialog._connectivity_transport = _mock_transport()
    dialog._on_accept()
    assert SettingsDialog().engine == ENGINE_ONLINE


def test_settings_dialog_persists_local_engine_config(qapp):
    """本地模型（比如 Ollama 部署的 Sakura）走单独一套配置，切换引擎后要能存住
    并在重开设置对话框后读回来，跟在线 provider 的字段互不干扰。"""
    dialog = SettingsDialog()
    index = dialog._engine_combo.findData(ENGINE_LOCAL)
    dialog._engine_combo.setCurrentIndex(index)
    assert not dialog._local_box.isHidden()
    assert dialog._online_box.isHidden()

    dialog._local_base_url_edit.setText("http://192.168.1.10:11434/v1")
    dialog._local_model_edit.setText("sakura-galtransl")
    dialog._connectivity_transport = _mock_transport()

    dialog._on_accept()

    reloaded = SettingsDialog()
    assert reloaded.engine == ENGINE_LOCAL
    assert reloaded.local_base_url == "http://192.168.1.10:11434/v1"
    assert reloaded.local_model == "sakura-galtransl"
    # 在线引擎切走之后 fallback 的说明框应该隐藏，不误导用户以为本地引擎也有故障转移
    assert reloaded._fallback_box.isHidden()


def _select_online_engine(dialog: SettingsDialog) -> None:
    # QSettings 跨测试共享同一个临时 ini（见 conftest.py），显式选中"在线"而不是依赖
    # 默认值，避免被前面把引擎切成本地的测试污染。
    dialog._engine_combo.setCurrentIndex(dialog._engine_combo.findData(ENGINE_ONLINE))


def test_settings_dialog_check_connectivity_succeeds_when_server_responds(qapp):
    """只要服务端有响应就算"连得上"，哪怕是 401（key 错）也不算连通性失败——
    key/模型名对不对是另一回事，留给真正翻译时的报错反馈。"""
    dialog = SettingsDialog()
    _select_online_engine(dialog)
    dialog._api_key_edit.setText("wrong-key")
    dialog._connectivity_transport = _mock_transport(status_code=401)

    ok, error = dialog._check_connectivity()

    assert ok is True
    assert error == ""


def test_settings_dialog_check_connectivity_fails_on_5xx_response(qapp):
    """502/504 这类网关错误不算"连通"——本机走系统代理时，代理能正常应答但连不上
    局域网里真正的目标地址会回这个，客户端确实收到了响应，但要连的地址其实没通。"""
    dialog = SettingsDialog()
    _select_online_engine(dialog)
    dialog._api_key_edit.setText("test-key")
    dialog._connectivity_transport = _mock_transport(status_code=502)

    ok, error = dialog._check_connectivity()

    assert ok is False
    assert "502" in error


def test_settings_dialog_check_connectivity_fails_on_transport_error(qapp):
    dialog = SettingsDialog()
    _select_online_engine(dialog)
    dialog._api_key_edit.setText("test-key")
    dialog._connectivity_transport = _unreachable_transport()

    ok, error = dialog._check_connectivity()

    assert ok is False
    assert error  # 带上了具体的错误信息，不是空字符串


def test_settings_dialog_check_connectivity_fails_when_online_api_key_empty(qapp):
    dialog = SettingsDialog()
    _select_online_engine(dialog)
    dialog._api_key_edit.setText("")
    dialog._connectivity_transport = _mock_transport()

    ok, error = dialog._check_connectivity()

    assert ok is False
    assert "API Key" in error


def test_settings_dialog_check_connectivity_fails_when_local_base_url_empty(qapp):
    dialog = SettingsDialog()
    index = dialog._engine_combo.findData(ENGINE_LOCAL)
    dialog._engine_combo.setCurrentIndex(index)
    dialog._local_base_url_edit.setText("")
    dialog._connectivity_transport = _mock_transport()

    ok, error = dialog._check_connectivity()

    assert ok is False
    assert "Base URL" in error


def test_settings_dialog_on_accept_does_not_save_when_connectivity_check_fails(
    qapp, monkeypatch
):
    """连通性检查没过时，_on_accept 不应该落盘设置——不能悄悄存下一个连不上的配置。"""
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)  # 测试环境没人点掉弹窗

    baseline = SettingsDialog().concurrency
    dialog = SettingsDialog()
    dialog._api_key_edit.setText("test-key")
    dialog._concurrency_spin.setValue(baseline + 1)
    dialog._connectivity_transport = _unreachable_transport()

    dialog._on_accept()

    assert dialog.result() != int(QDialog.DialogCode.Accepted)
    assert SettingsDialog().concurrency == baseline


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
