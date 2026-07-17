from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel

EngineName = Literal["mv", "mz", "vxace"]
TranslationStatus = Literal["pending", "translated", "reviewed"]


class TextUnit(BaseModel):
    id: str
    engine: EngineName
    file_path: str
    locator: str
    context: str
    source_text: str
    control_code_map: dict[str, str] = {}
    translated_text: str | None = None
    status: TranslationStatus = "pending"


def compute_text_unit_id(engine: EngineName, file_path: str, locator: str) -> str:
    raw = f"{engine}:{file_path}:{locator}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_source_hash(source_text: str) -> str:
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()
