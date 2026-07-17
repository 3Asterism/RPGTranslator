from __future__ import annotations

from pathlib import Path
from typing import Callable

from rpg_translator.core.ir import TextUnit
from rpg_translator.core.store import Store
from rpg_translator.engines.base import EngineAdapter
from rpg_translator.engines.mv_mz import MVAdapter, MZAdapter
from rpg_translator.engines.vxace import VXAceAdapter
from rpg_translator.translate.batch_translator import translate_units
from rpg_translator.translate.glossary import extract_glossary_candidates
from rpg_translator.translate.llm_client import LLMClient, LLMConfig
from rpg_translator.translate.qa import ConflictRow, export_conflicts_csv, find_context_conflicts

REGISTERED_ADAPTERS: list[type[EngineAdapter]] = [MVAdapter, MZAdapter, VXAceAdapter]


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


def _build_llm_configs(
    api_key: str,
    base_url: str,
    model: str,
    fallback_api_key: str | None = None,
    fallback_base_url: str | None = None,
    fallback_model: str | None = None,
) -> list[LLMConfig]:
    configs = [LLMConfig(api_key=api_key, base_url=base_url, model=model)]
    if fallback_api_key and fallback_base_url and fallback_model:
        configs.append(
            LLMConfig(api_key=fallback_api_key, base_url=fallback_base_url, model=fallback_model)
        )
    return configs


async def run_glossary(
    db_path: Path,
    api_key: str | None,
    base_url: str,
    model: str,
    fallback_api_key: str | None = None,
    fallback_base_url: str | None = None,
    fallback_model: str | None = None,
) -> dict[str, str]:
    api_key = _require_api_key(api_key)
    with Store(db_path) as store:
        units = store.list_units()
        configs = _build_llm_configs(
            api_key, base_url, model, fallback_api_key, fallback_base_url, fallback_model
        )
        async with LLMClient(configs) as client:
            candidates = await extract_glossary_candidates(client, units)
        store.set_glossary(candidates)
    return candidates


async def run_translate(
    db_path: Path,
    api_key: str | None,
    base_url: str,
    model: str,
    concurrency: int,
    on_progress: Callable[[int, int], None] | None = None,
    fallback_api_key: str | None = None,
    fallback_base_url: str | None = None,
    fallback_model: str | None = None,
) -> list[TextUnit]:
    api_key = _require_api_key(api_key)
    with Store(db_path) as store:
        pending = store.list_units(status="pending")
        glossary = store.get_glossary()
        configs = _build_llm_configs(
            api_key, base_url, model, fallback_api_key, fallback_base_url, fallback_model
        )
        async with LLMClient(configs) as client:
            await translate_units(
                client, store, pending, glossary, concurrency, on_progress=on_progress
            )
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
    on_stage: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    fallback_api_key: str | None = None,
    fallback_base_url: str | None = None,
    fallback_model: str | None = None,
) -> list[TextUnit]:
    """extract -> 术语抽取 -> translate -> inject 完整链路。

    on_stage(message) 在阶段切换时调用一次；on_progress(completed, total) 在翻译阶段
    每完成一个去重分组时调用一次。两者都是可选的，GUI 用它们驱动进度条/日志，CLI 不传。
    """
    api_key = _require_api_key(api_key)
    adapter = detect_adapter(project_dir)

    if on_stage is not None:
        on_stage("提取中…")
    units = adapter.extract(project_dir)

    with Store(db_path) as store:
        store.upsert_units(units)
        configs = _build_llm_configs(
            api_key, base_url, model, fallback_api_key, fallback_base_url, fallback_model
        )
        async with LLMClient(configs) as client:
            if on_stage is not None:
                on_stage("术语抽取中…")
            glossary = await extract_glossary_candidates(client, units)
            store.set_glossary(glossary)

            pending = store.list_units(status="pending")
            if on_stage is not None:
                on_stage(f"翻译中（共 {len(pending)} 条待译）…")
            await translate_units(
                client, store, pending, glossary, concurrency, on_progress=on_progress
            )
        all_units = store.list_units()

    if on_stage is not None:
        on_stage("写回中…")
    adapter.inject(project_dir, all_units, output_dir)
    return all_units


def run_qa(db_path: Path, export_path: Path | None) -> list[ConflictRow]:
    with Store(db_path) as store:
        conflicts = find_context_conflicts(store)
    if export_path is not None:
        export_conflicts_csv(conflicts, export_path)
    return conflicts
