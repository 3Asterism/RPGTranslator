from __future__ import annotations

import csv
from pathlib import Path
from typing import TypedDict

from rpg_translator.core.store import Store

_CSV_FIELDNAMES = ["source_text", "file_path", "locator", "context", "translated_text"]


class ConflictRow(TypedDict):
    source_text: str
    file_path: str
    locator: str
    context: str
    translated_text: str


def find_context_conflicts(store: Store) -> list[ConflictRow]:
    """找出同一 source_text（复用了同一份翻译记忆）却出现在不同 context 里的候选冲突。

    这里只做机械的分组启发式，不判断这些 context 是否真的语义冲突——按 spec 要求，
    这类判断留给人工复核，导出列表即可。
    """
    units = store.list_units()
    by_source: dict[str, list] = {}
    for unit in units:
        by_source.setdefault(unit.source_text, []).append(unit)

    conflicts: list[ConflictRow] = []
    for source_text, group in by_source.items():
        distinct_contexts = {u.context for u in group if u.context}
        if len(group) > 1 and len(distinct_contexts) > 1:
            for unit in group:
                conflicts.append(
                    {
                        "source_text": source_text,
                        "file_path": unit.file_path,
                        "locator": unit.locator,
                        "context": unit.context,
                        "translated_text": unit.translated_text or "",
                    }
                )
    return conflicts


def export_conflicts_csv(conflicts: list[ConflictRow], export_path: Path) -> None:
    with open(export_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(conflicts)
