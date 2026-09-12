from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rpg_translator.engines import wolf_archive


def _make_packed_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "game"
    project_dir.mkdir()
    (project_dir / "Data.wolf").write_bytes(b"\x00" * 16)
    (project_dir / "Config.exe").write_bytes(b"\x00" * 8)
    (project_dir / "MyGame.exe").write_bytes(b"\x00" * 64)  # 体积比 Config.exe 大，应该被选中
    return project_dir


def test_is_packed_wolf_project_true_when_only_archive_present(tmp_path: Path):
    project_dir = _make_packed_project(tmp_path)
    assert wolf_archive.is_packed_wolf_project(project_dir) is True


def test_is_packed_wolf_project_false_when_already_unpacked(tmp_path: Path):
    project_dir = _make_packed_project(tmp_path)
    (project_dir / "Data" / "BasicData").mkdir(parents=True)
    assert wolf_archive.is_packed_wolf_project(project_dir) is False


def test_is_packed_wolf_project_false_without_archive(tmp_path: Path):
    project_dir = tmp_path / "game"
    project_dir.mkdir()
    assert wolf_archive.is_packed_wolf_project(project_dir) is False


def test_ensure_wolf_unpacked_noop_when_not_packed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_dir = tmp_path / "game"
    project_dir.mkdir()
    called = False

    def _boom(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("不应该走到 subprocess 这一步")

    monkeypatch.setattr(wolf_archive.subprocess, "run", _boom)
    assert wolf_archive.ensure_wolf_unpacked(project_dir) is False
    assert called is False


def test_ensure_wolf_unpacked_picks_largest_non_utility_exe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_dir = _make_packed_project(tmp_path)
    monkeypatch.setattr(wolf_archive, "find_uberwolf_cli", lambda: Path("dummy_uberwolf.exe"))

    seen_args = {}

    def fake_run(args, cwd, capture_output, text, encoding, errors, timeout):
        seen_args["args"] = args
        seen_args["cwd"] = cwd
        (project_dir / "Data" / "BasicData").mkdir(parents=True)
        return subprocess.CompletedProcess(args, 0, stdout="Done", stderr="")

    monkeypatch.setattr(wolf_archive.subprocess, "run", fake_run)

    result = wolf_archive.ensure_wolf_unpacked(project_dir)

    assert result is True
    assert seen_args["args"][0] == "dummy_uberwolf.exe"
    assert seen_args["args"][1] == "-o"
    assert seen_args["args"][2] == str(project_dir / "MyGame.exe")
    assert not (project_dir / "Data.wolf").exists()
    assert (project_dir / ".rpg_translator_backup" / "Data.wolf").is_file()


def test_ensure_wolf_unpacked_raises_when_cli_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_dir = _make_packed_project(tmp_path)
    monkeypatch.setattr(wolf_archive, "find_uberwolf_cli", lambda: None)

    with pytest.raises(wolf_archive.WolfArchiveError, match="UberWolfCli"):
        wolf_archive.ensure_wolf_unpacked(project_dir)


def test_ensure_wolf_unpacked_raises_when_no_game_exe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_dir = tmp_path / "game"
    project_dir.mkdir()
    (project_dir / "Data.wolf").write_bytes(b"\x00")
    (project_dir / "Config.exe").write_bytes(b"\x00")  # 唯一的 exe 在排除名单里
    monkeypatch.setattr(wolf_archive, "find_uberwolf_cli", lambda: Path("dummy_uberwolf.exe"))

    with pytest.raises(wolf_archive.WolfArchiveError, match="游戏本体 exe"):
        wolf_archive.ensure_wolf_unpacked(project_dir)


def test_ensure_wolf_unpacked_raises_on_subprocess_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_dir = _make_packed_project(tmp_path)
    monkeypatch.setattr(wolf_archive, "find_uberwolf_cli", lambda: Path("dummy_uberwolf.exe"))

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr(wolf_archive.subprocess, "run", fake_run)

    with pytest.raises(wolf_archive.WolfArchiveError, match="失败"):
        wolf_archive.ensure_wolf_unpacked(project_dir)
    assert (project_dir / "Data.wolf").is_file()  # 失败时原文件不应该被挪走
