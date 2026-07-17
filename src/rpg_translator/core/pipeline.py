from __future__ import annotations

from pathlib import Path

from rpg_translator.core.ir import TextUnit
from rpg_translator.core.store import Store
from rpg_translator.engines.base import EngineAdapter
from rpg_translator.engines.mv_mz import MVAdapter, MZAdapter

REGISTERED_ADAPTERS: list[type[EngineAdapter]] = [MVAdapter, MZAdapter]


class UnknownEngineError(Exception):
    pass


def detect_adapter(project_dir: Path) -> EngineAdapter:
    for adapter_cls in REGISTERED_ADAPTERS:
        if adapter_cls.detect(project_dir):
            return adapter_cls()
    raise UnknownEngineError(f"未识别到支持的 RPG Maker 引擎：{project_dir}")


def run_extract(project_dir: Path, db_path: Path) -> list[TextUnit]:
    adapter = detect_adapter(project_dir)
    units = adapter.extract(project_dir)
    with Store(db_path) as store:
        store.upsert_units(units)
    return units


def run_inject(project_dir: Path, db_path: Path, output_dir: Path) -> list[TextUnit]:
    adapter = detect_adapter(project_dir)
    with Store(db_path) as store:
        units = store.list_units()
    adapter.inject(project_dir, units, output_dir)
    return units
