from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Backend = Literal["mono", "il2cpp"]
Arch = Literal["x86", "x64"]

_DATA_MARKER_NAMES = ("globalgamemanagers", "data.unity3d")


@dataclass(frozen=True)
class UnityTarget:
    exe_path: Path
    data_dir: Path
    backend: Backend
    arch: Arch


class InvalidPEFileError(ValueError):
    pass


def _find_unity_data_dir(project_dir: Path) -> tuple[Path, Path] | None:
    """找 <ExeName>.exe 同级的 <ExeName>_Data/ 目录，且其中含 globalgamemanagers
    或 data.unity3d 才算数（避免把同名普通文件夹误判）。目录下可能有多个 exe
    （比如自带的 UnityCrashHandler64.exe），逐个试，第一个配对成功的即为主程序。"""
    for exe_path in sorted(project_dir.glob("*.exe")):
        data_dir = project_dir / f"{exe_path.stem}_Data"
        if not data_dir.is_dir():
            continue
        if any((data_dir / name).exists() for name in _DATA_MARKER_NAMES):
            return exe_path, data_dir
    return None


def _detect_backend(exe_path: Path, data_dir: Path) -> Backend | None:
    if (exe_path.parent / "GameAssembly.dll").is_file():
        return "il2cpp"
    if (data_dir / "Managed" / "Assembly-CSharp.dll").is_file():
        return "mono"
    return None


def _detect_arch(exe_path: Path) -> Arch:
    """读 PE 头 Machine 字段判定位数，不引入 pefile 依赖。DOS 头 e_lfanew（偏移
    0x3C 处 4 字节小端）指向 PE 头起始；PE 头是 4 字节签名 b"PE\\0\\0" 紧跟 2 字节
    Machine 字段（IMAGE_FILE_HEADER 第一个字段）。"""
    with exe_path.open("rb") as f:
        dos_header = f.read(64)
        if len(dos_header) < 64 or dos_header[:2] != b"MZ":
            raise InvalidPEFileError(f"{exe_path} 不是合法的 PE 文件（缺少 MZ 头）")
        (e_lfanew,) = struct.unpack_from("<I", dos_header, 0x3C)
        f.seek(e_lfanew)
        pe_header = f.read(6)
        if len(pe_header) < 6 or pe_header[:4] != b"PE\x00\x00":
            raise InvalidPEFileError(f"{exe_path} 不是合法的 PE 文件（缺少 PE 签名）")
        (machine,) = struct.unpack_from("<H", pe_header, 4)
    if machine == 0x014C:
        return "x86"
    if machine == 0x8664:
        return "x64"
    raise InvalidPEFileError(f"{exe_path} 是不支持的架构（machine=0x{machine:04x}），只支持 x86/x64")


def detect_unity(project_dir: Path) -> UnityTarget | None:
    found = _find_unity_data_dir(project_dir)
    if found is None:
        return None
    exe_path, data_dir = found
    backend = _detect_backend(exe_path, data_dir)
    if backend is None:
        return None
    try:
        arch = _detect_arch(exe_path)
    except InvalidPEFileError:
        return None
    return UnityTarget(exe_path=exe_path, data_dir=data_dir, backend=backend, arch=arch)
