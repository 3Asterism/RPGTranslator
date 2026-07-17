from __future__ import annotations

from pathlib import Path
from typing import Any

from rubymarshal.reader import load
from rubymarshal.writer import writes


def read_rvdata2(path: Path) -> Any:
    with open(path, "rb") as f:
        return load(f)


def write_rvdata2(path: Path, obj: Any) -> None:
    path.write_bytes(writes(obj))
