from __future__ import annotations

import csv
from pathlib import Path
from typing import TypedDict

from rpg_translator.core.store import Store

_CSV_FIELDNAMES = [
    "source_text",
    "file_path",
    "locator",
    "context",
    "context_group",
    "translated_text",
]


class ConflictRow(TypedDict):
    source_text: str
    file_path: str
    locator: str
    context: str
    context_group: str
    translated_text: str


def _scene_key(unit) -> str:
    # context_group（事件页面 id）现在是主要的"这句话来自哪个场景"信号——大部分
    # 台词类条目的 context 字段本身已经不再携带场景描述（改成靠段落打包提供上下文，
    # 见 CLAUDE.md）。没有 context_group 的条目（数据库字段这类）退回用 context
    # 本身的静态描述（比如"数据库记录：xxx"）区分场景，兼容旧行为。
    return unit.context_group or unit.context


def find_context_conflicts(store: Store) -> list[ConflictRow]:
    """找出同一 source_text（复用了同一份翻译记忆）却出现在不同场景里的候选冲突。

    这里只做机械的分组启发式，不判断这些场景是否真的语义冲突——按 spec 要求，
    这类判断留给人工复核，导出列表即可。
    """
    units = store.list_units()
    by_source: dict[str, list] = {}
    for unit in units:
        by_source.setdefault(unit.source_text, []).append(unit)

    conflicts: list[ConflictRow] = []
    for source_text, group in by_source.items():
        distinct_keys = {_scene_key(u) for u in group if _scene_key(u)}
        if len(group) > 1 and len(distinct_keys) > 1:
            for unit in group:
                conflicts.append(
                    {
                        "source_text": source_text,
                        "file_path": unit.file_path,
                        "locator": unit.locator,
                        "context": unit.context,
                        "context_group": unit.context_group,
                        "translated_text": unit.translated_text or "",
                    }
                )
    return conflicts


def export_conflicts_csv(conflicts: list[ConflictRow], export_path: Path) -> None:
    with open(export_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(conflicts)
