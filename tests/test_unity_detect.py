from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rpg_translator.unity.detect import UnityTarget, detect_unity


def _pe_bytes(machine: int) -> bytes:
    """构造一个最小的、能通过 detect_unity 里 PE 头解析的假 exe 字节串：64 字节
    DOS 头（e_lfanew 指向紧随其后的 PE 头）+ 4 字节 PE 签名 + 2 字节 Machine 字段。
    detect_unity 只读前 70 字节，不需要更完整的 PE 结构。"""
    dos_header = bytearray(64)
    dos_header[0:2] = b"MZ"
    struct.pack_into("<I", dos_header, 0x3C, 64)
    pe_header = b"PE\x00\x00" + struct.pack("<H", machine) + b"\x00" * 18
    return bytes(dos_header) + pe_header


def _write_exe(path: Path, machine: int) -> None:
    path.write_bytes(_pe_bytes(machine))


def _make_mono_project(tmp_path: Path, machine: int = 0x8664) -> Path:
    exe = tmp_path / "Game.exe"
    _write_exe(exe, machine)
    data_dir = tmp_path / "Game_Data"
    (data_dir / "Managed").mkdir(parents=True)
    (data_dir / "globalgamemanagers").write_bytes(b"\x00")
    (data_dir / "Managed" / "Assembly-CSharp.dll").write_bytes(b"\x00")
    return tmp_path


def _make_il2cpp_project(tmp_path: Path, machine: int = 0x8664) -> Path:
    exe = tmp_path / "Game.exe"
    _write_exe(exe, machine)
    data_dir = tmp_path / "Game_Data"
    data_dir.mkdir(parents=True)
    (data_dir / "data.unity3d").write_bytes(b"\x00")
    (tmp_path / "GameAssembly.dll").write_bytes(b"\x00")
    return tmp_path


def test_detect_unity_mono_x64(tmp_path: Path):
    _make_mono_project(tmp_path, machine=0x8664)

    target = detect_unity(tmp_path)

    assert target is not None
    assert target.backend == "mono"
    assert target.arch == "x64"
    assert target.exe_path == tmp_path / "Game.exe"
    assert target.data_dir == tmp_path / "Game_Data"


def test_detect_unity_mono_x86(tmp_path: Path):
    _make_mono_project(tmp_path, machine=0x014C)

    target = detect_unity(tmp_path)

    assert target is not None
    assert target.arch == "x86"


def test_detect_unity_il2cpp_x64(tmp_path: Path):
    _make_il2cpp_project(tmp_path, machine=0x8664)

    target = detect_unity(tmp_path)

    assert target is not None
    assert target.backend == "il2cpp"
    assert target.arch == "x64"


def test_detect_unity_returns_none_for_non_unity_dir(tmp_path: Path):
    (tmp_path / "readme.txt").write_text("not a game", encoding="utf-8")

    assert detect_unity(tmp_path) is None


def test_detect_unity_returns_none_when_backend_unknown(tmp_path: Path):
    """有 Unity 目录结构特征，但既没有 GameAssembly.dll 也没有
    Managed/Assembly-CSharp.dll——判不出 Mono/IL2CPP，不瞎猜，返回 None。"""
    exe = tmp_path / "Game.exe"
    _write_exe(exe, 0x8664)
    data_dir = tmp_path / "Game_Data"
    data_dir.mkdir()
    (data_dir / "globalgamemanagers").write_bytes(b"\x00")

    assert detect_unity(tmp_path) is None


def test_detect_unity_skips_non_unity_exe_and_finds_real_one(tmp_path: Path):
    """目录下可能有多个 exe（比如 crash handler），第一个配不上 _Data 目录的
    要跳过，继续找下一个。"""
    crash_handler = tmp_path / "UnityCrashHandler64.exe"
    _write_exe(crash_handler, 0x8664)

    _make_mono_project(tmp_path)

    target = detect_unity(tmp_path)

    assert target is not None
    assert target.exe_path == tmp_path / "Game.exe"
