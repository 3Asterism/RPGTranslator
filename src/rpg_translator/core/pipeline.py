from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, Literal

from rpg_translator.core.ir import TextUnit
from rpg_translator.core.store import Store
from rpg_translator.engines.base import EngineAdapter
from rpg_translator.engines.mv_mz import MVAdapter, MZAdapter
from rpg_translator.engines.vxace import VXAceAdapter
from rpg_translator.engines.wolf import WolfAdapter
from rpg_translator.engines.xp_vx import VXAdapter, XPAdapter
from rpg_translator.translate.batch_translator import DEFAULT_BATCH_SIZE, translate_units
from rpg_translator.translate.llm_client import LLMClient, LLMConfig
from rpg_translator.translate.qa import ConflictRow, export_conflicts_csv, find_context_conflicts

REGISTERED_ADAPTERS: list[type[EngineAdapter]] = [
    MVAdapter,
    MZAdapter,
    VXAceAdapter,
    XPAdapter,
    VXAdapter,
    WolfAdapter,
]


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
    _stash_language_variants(project_dir, output_dir, units)
    return units


_BACKUP_DIR_NAME = ".rpg_translator_backup"
LanguageVariant = Literal["original", "translated"]


def _backup_variant_dir(output_dir: Path, variant: LanguageVariant) -> Path:
    return output_dir / _BACKUP_DIR_NAME / variant


def _stash_language_variants(project_dir: Path, output_dir: Path, units: list[TextUnit]) -> None:
    """inject 完之后，把改动过的文本文件（不是整个工程——素材文件两个语言版本共用，
    没必要重复占磁盘）分别留一份"原文"和"译文"快照，供 switch_language() 一键切换用。
    方便中日对照校对，或者翻译效果有问题时先切回原文确认是不是翻译本身的锅，不用重新
    跑一遍 inject。"""
    touched_files = sorted({u.file_path for u in units})
    for rel_path in touched_files:
        original_src = project_dir / rel_path
        if original_src.is_file():
            dest = _backup_variant_dir(output_dir, "original") / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original_src, dest)

        translated_src = output_dir / rel_path
        if translated_src.is_file():
            dest = _backup_variant_dir(output_dir, "translated") / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(translated_src, dest)


def has_language_variant(output_dir: Path, variant: LanguageVariant) -> bool:
    return _backup_variant_dir(output_dir, variant).is_dir()


def switch_language(output_dir: Path, variant: LanguageVariant) -> int:
    """把 output_dir 里的文本文件整体切换成 variant 版本（原文/译文），从 inject 时
    留下的快照拷回，不用重新跑一遍提取/注入。返回实际切换的文件数。"""
    stash_dir = _backup_variant_dir(output_dir, variant)
    if not stash_dir.is_dir():
        raise FileNotFoundError(
            f"{output_dir} 下没有找到 {variant} 版本的备份，请先跑一次注入。"
        )
    count = 0
    for src in stash_dir.rglob("*"):
        if src.is_file():
            rel = src.relative_to(stash_dir)
            dest = output_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            count += 1
    return count


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
    cancel_check: Callable[[], bool] | None = None,
    on_usage: Callable[[str, int, int], None] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[list[TextUnit], list[tuple[str, str]]]:
    """只翻 status="pending" 的条目——中途停止或意外中断后重新调用，已经翻译过的
    （包括这次停止前刚落盘的那些）不会被重新送去调用 API，这是断点续传在翻译这一层
    的体现（配合 store.upsert_units 不覆盖已翻译进度，见 core/store.py）。

    cancel_check() 每个批次派发前检查一次，返回 True 就不再发起新的翻译请求；已经在等
    响应的请求也会被主动打断，不是傻等它跑完（见 translate_units/_chat_cancellable）。

    on_usage(model, prompt_tokens, completion_tokens) 每次 LLM 调用成功后回调一次，
    供 GUI 实时统计 token 用量/预估花费，不传就跳过。

    返回 (已翻译的 TextUnit 列表, 失败条目列表)。失败条目（比如被内容审核拒绝、或所有
    provider 都报错的条目）不会中断整体翻译，保持 status="pending" 供下次重跑续译，
    详见 translate_units 的说明。"""
    api_key = _require_api_key(api_key)
    with Store(db_path) as store:
        pending = store.list_units(status="pending")
        configs = _build_llm_configs(
            api_key, base_url, model, fallback_api_key, fallback_base_url, fallback_model
        )
        async with LLMClient(configs, on_usage=on_usage) as client:
            failures = await translate_units(
                client,
                store,
                pending,
                concurrency,
                on_progress=on_progress,
                cancel_check=cancel_check,
                batch_size=batch_size,
            )
        translated = store.list_units(status="translated")
    return translated, failures


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
    on_usage: Callable[[str, int, int], None] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[TextUnit]:
    """extract -> translate -> inject 完整链路。

    on_stage(message) 在阶段切换时调用一次；on_progress(completed, total) 在翻译阶段
    每完成一个去重分组时调用一次；on_usage(model, prompt_tokens, completion_tokens) 在
    每次 LLM 调用成功后调用一次。三者都是可选的，GUI 用它们驱动进度条/日志/花费统计，
    CLI 不传。
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
        async with LLMClient(configs, on_usage=on_usage) as client:
            pending = store.list_units(status="pending")
            if on_stage is not None:
                on_stage(f"翻译中（共 {len(pending)} 条待译）…")
            failures = await translate_units(
                client,
                store,
                pending,
                concurrency,
                on_progress=on_progress,
                batch_size=batch_size,
            )
            if on_stage is not None and failures:
                on_stage(f"{len(failures)} 条翻译失败已跳过（保留待译状态，可重跑续译）")
        all_units = store.list_units()

    if on_stage is not None:
        on_stage("写回中…")
    adapter.inject(project_dir, all_units, output_dir)
    _stash_language_variants(project_dir, output_dir, all_units)
    return all_units


def run_qa(db_path: Path, export_path: Path | None) -> list[ConflictRow]:
    with Store(db_path) as store:
        conflicts = find_context_conflicts(store)
    if export_path is not None:
        export_conflicts_csv(conflicts, export_path)
    return conflicts


_PACKAGE_FORMAT_VERSION = 1


def export_translation_package(db_path: Path, game_name: str, dest_dir: Path) -> Path:
    """把已翻译内容导出成一份轻量、可分享的翻译包（只含译文数据，不含游戏本体文件），
    按游戏名命名方便区分。别人拿着同一版本的游戏，在自己电脑上跑一遍拖拽识别（即使
    还没点翻译）后，用 import_translation_package 直接把这份译文套进去——不用重新调用
    一遍翻译 API，省他们的 token 也省时间。"""
    with Store(db_path) as store:
        units = store.list_units()

    translated = [u for u in units if u.translated_text is not None]
    package = {
        "format_version": _PACKAGE_FORMAT_VERSION,
        "game_name": game_name,
        "units": [
            {
                "id": u.id,
                "file_path": u.file_path,
                "source_text": u.source_text,
                "translated_text": u.translated_text,
                "status": u.status,
            }
            for u in translated
        ],
    }

    dest_dir.mkdir(parents=True, exist_ok=True)
    package_path = dest_dir / f"{game_name}.rpgtrans.json"
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return package_path


def import_translation_package(db_path: Path, package_path: Path) -> tuple[int, int]:
    """导入别人分享的翻译包。TextUnit.id 是 engine+file_path+locator 算出来的哈希
    （见 core/ir.py compute_text_unit_id），只要双方是同一版本的游戏，各自跑 extract
    出来的 id 天然一致，不需要额外的模糊匹配。source_text 对不上的（说明本地这份游戏
    版本和分享者的不一致，文本已经变了）跳过，不能张冠李戴地硬套一个可能过时的译文。
    返回 (成功导入条数, 因版本不匹配被跳过条数)。"""
    data = json.loads(package_path.read_text(encoding="utf-8"))

    imported = 0
    skipped = 0
    with Store(db_path) as store:
        for entry in data.get("units", []):
            local_unit = store.get_unit(entry["id"])
            if local_unit is None or local_unit.source_text != entry["source_text"]:
                skipped += 1
                continue
            store.update_translation(
                entry["id"], entry["translated_text"], status=entry.get("status", "translated")
            )
            imported += 1

    return imported, skipped
