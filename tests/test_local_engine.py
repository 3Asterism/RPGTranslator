from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from rpg_translator.translate import local_engine
from rpg_translator.translate.local_engine import (
    BundledEngine,
    LocalEngineProcess,
    find_bundled_engine,
    get_app_root,
)


def _make_bundled_files(root: Path) -> BundledEngine:
    engine_dir = root / "resources" / "local_engine"
    engine_dir.mkdir(parents=True)
    exe_path = engine_dir / "llama-server.exe"
    model_path = engine_dir / "sakura-7b-qwen2.5-v1.0-q6k.gguf"
    exe_path.write_bytes(b"fake exe")
    model_path.write_bytes(b"fake gguf")
    return BundledEngine(exe_path=exe_path, model_path=model_path)


def test_find_bundled_engine_returns_none_when_files_missing(tmp_path: Path):
    assert find_bundled_engine(tmp_path) is None


def test_find_bundled_engine_returns_none_when_only_exe_present(tmp_path: Path):
    engine_dir = tmp_path / "resources" / "local_engine"
    engine_dir.mkdir(parents=True)
    (engine_dir / "llama-server.exe").write_bytes(b"fake exe")
    assert find_bundled_engine(tmp_path) is None


def test_find_bundled_engine_returns_bundle_when_both_files_present(tmp_path: Path):
    expected = _make_bundled_files(tmp_path)
    found = find_bundled_engine(tmp_path)
    assert found == expected


def test_get_app_root_not_frozen_points_at_project_root():
    # 开发环境（没走 PyInstaller）应该落到项目根目录——用 pyproject.toml 存在
    # 与否验证，而不是猜测具体的层级关系。
    assert (get_app_root() / "pyproject.toml").is_file()


class _FakeProcess:
    def __init__(self):
        self.terminated = 0
        self.killed = 0
        self._exited = False

    def poll(self):
        return 0 if self._exited else None

    def terminate(self):
        self.terminated += 1
        self._exited = True

    def kill(self):
        self.killed += 1
        self._exited = True

    def wait(self, timeout=None):
        return 0


def _patch_popen(monkeypatch, fake_process: _FakeProcess):
    calls: list[list[str]] = []

    def fake_popen(args, **kwargs):
        calls.append(args)
        return fake_process

    monkeypatch.setattr(local_engine.subprocess, "Popen", fake_popen)
    return calls


def test_start_spawns_process_and_returns_localhost_base_url(tmp_path: Path, monkeypatch):
    engine = _make_bundled_files(tmp_path)
    fake_process = _FakeProcess()
    calls = _patch_popen(monkeypatch, fake_process)

    proc = LocalEngineProcess(engine)
    base_url = proc.start()

    assert base_url.startswith("http://127.0.0.1:")
    assert base_url.endswith("/v1")
    assert proc.is_running() is True
    assert proc.log_path is not None and proc.log_path.exists()
    assert len(calls) == 1
    assert calls[0][0] == str(engine.exe_path)
    assert "-m" in calls[0] and str(engine.model_path) in calls[0]


def test_start_is_idempotent_while_already_running(tmp_path: Path, monkeypatch):
    engine = _make_bundled_files(tmp_path)
    fake_process = _FakeProcess()
    calls = _patch_popen(monkeypatch, fake_process)

    proc = LocalEngineProcess(engine)
    first_url = proc.start()
    second_url = proc.start()

    assert first_url == second_url
    assert len(calls) == 1  # 第二次 start() 没有再起一个新子进程


def test_wait_until_ready_returns_true_when_server_responds(tmp_path: Path, monkeypatch):
    engine = _make_bundled_files(tmp_path)
    fake_process = _FakeProcess()
    _patch_popen(monkeypatch, fake_process)

    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    proc = LocalEngineProcess(engine, transport=transport)
    proc.start()

    assert proc.wait_until_ready(timeout=5.0) is True


def test_wait_until_ready_returns_false_fast_when_process_already_exited(tmp_path: Path, monkeypatch):
    engine = _make_bundled_files(tmp_path)
    fake_process = _FakeProcess()
    fake_process._exited = True  # 模拟子进程启动后立刻崩溃退出
    _patch_popen(monkeypatch, fake_process)

    proc = LocalEngineProcess(engine)
    proc.start()

    assert proc.wait_until_ready(timeout=5.0) is False


def test_wait_until_ready_returns_false_when_never_reachable(tmp_path: Path, monkeypatch):
    engine = _make_bundled_files(tmp_path)
    fake_process = _FakeProcess()
    _patch_popen(monkeypatch, fake_process)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    proc = LocalEngineProcess(engine, transport=transport)
    proc.start()

    assert proc.wait_until_ready(timeout=0.6) is False


def test_stop_is_noop_when_never_started(tmp_path: Path):
    engine = _make_bundled_files(tmp_path)
    proc = LocalEngineProcess(engine)
    proc.stop()  # 不应该抛异常


def test_stop_terminates_process_and_is_idempotent(tmp_path: Path, monkeypatch):
    engine = _make_bundled_files(tmp_path)
    fake_process = _FakeProcess()
    _patch_popen(monkeypatch, fake_process)

    proc = LocalEngineProcess(engine)
    proc.start()
    proc.stop()
    proc.stop()  # 第二次调用应该是 no-op，不重复 terminate

    assert fake_process.terminated == 1
    assert proc.is_running() is False
