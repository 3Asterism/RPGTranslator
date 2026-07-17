from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel

EngineName = Literal["mv", "mz", "vxace", "xp", "vx", "wolf"]
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
    # VX Ace/XP/VX 的 Show Text 指令一条消息可以有多行（每行是命令列表里单独一条
    # 401 指令），这些行合并成一个 TextUnit 一起送翻译（spec 第 9 节，不要逐行翻译）。
    # extra_locators 是除 locator（第一行）之外，其余行的写回位置，顺序对应原始行序。
    # 空列表就是普通单行 TextUnit，其他引擎/字段不用管这个字段。
    extra_locators: list[str] = []


def compute_text_unit_id(engine: EngineName, file_path: str, locator: str) -> str:
    raw = f"{engine}:{file_path}:{locator}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_source_hash(source_text: str) -> str:
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()
