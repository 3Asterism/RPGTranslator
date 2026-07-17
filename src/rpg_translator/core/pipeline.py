from __future__ import annotations

from pathlib import Path

from rpg_translator.core.ir import TextUnit
from rpg_translator.core.store import Store
from rpg_translator.engines.base import EngineAdapter
from rpg_translator.engines.mv_mz import MVAdapter, MZAdapter
from rpg_translator.translate.batch_translator import translate_units
from rpg_translator.translate.glossary import extract_glossary_candidates
from rpg_translator.translate.llm_client import LLMClient, LLMConfig
from rpg_translator.translate.qa import ConflictRow, export_conflicts_csv, find_context_conflicts

REGISTERED_ADAPTERS: list[type[EngineAdapter]] = [MVAdapter, MZAdapter]


class UnknownEngineError(Exception):
    pass


class MissingApiKeyError(Exception):
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


def _require_api_key(api_key: str | None) -> str:
    if not api_key:
        raise MissingApiKeyError(
            "未配置 DeepSeek API Key，请先通过 keyring（GUI 设置面板）或环境变量 "
            "DEEPSEEK_API_KEY 设置。"
        )
    return api_key


async def run_glossary(
    db_path: Path, api_key: str | None, base_url: str, model: str
) -> dict[str, str]:
    api_key = _require_api_key(api_key)
    with Store(db_path) as store:
        units = store.list_units()
        config = LLMConfig(api_key=api_key, base_url=base_url, model=model)
        async with LLMClient(config) as client:
            candidates = await extract_glossary_candidates(client, units)
        store.set_glossary(candidates)
    return candidates


async def run_translate(
    db_path: Path,
    api_key: str | None,
    base_url: str,
    model: str,
    concurrency: int,
) -> list[TextUnit]:
    api_key = _require_api_key(api_key)
    with Store(db_path) as store:
        pending = store.list_units(status="pending")
        glossary = store.get_glossary()
        config = LLMConfig(api_key=api_key, base_url=base_url, model=model)
        async with LLMClient(config) as client:
            await translate_units(client, store, pending, glossary, concurrency)
        translated = store.list_units(status="translated")
    return translated


async def run_full(
    project_dir: Path,
    db_path: Path,
    output_dir: Path,
    api_key: str | None,
    base_url: str,
    model: str,
    concurrency: int,
) -> list[TextUnit]:
    """extract -> 术语抽取 -> translate -> inject 完整链路。"""
    api_key = _require_api_key(api_key)
    adapter = detect_adapter(project_dir)
    units = adapter.extract(project_dir)

    with Store(db_path) as store:
        store.upsert_units(units)
        config = LLMConfig(api_key=api_key, base_url=base_url, model=model)
        async with LLMClient(config) as client:
            glossary = await extract_glossary_candidates(client, units)
            store.set_glossary(glossary)
            pending = store.list_units(status="pending")
            await translate_units(client, store, pending, glossary, concurrency)
        all_units = store.list_units()

    adapter.inject(project_dir, all_units, output_dir)
    return all_units


def run_qa(db_path: Path, export_path: Path | None) -> list[ConflictRow]:
    with Store(db_path) as store:
        conflicts = find_context_conflicts(store)
    if export_path is not None:
        export_conflicts_csv(conflicts, export_path)
    return conflicts
