from __future__ import annotations

import asyncio
import re
from typing import Callable, NamedTuple

from rpg_translator.codec.control_codes import protect, restore
from rpg_translator.core.ir import TextUnit, compute_source_hash
from rpg_translator.core.store import Store
from rpg_translator.translate.llm_client import LLMClient

_TRANSLATE_SYSTEM_PROMPT_TEMPLATE = (
    "你是一个专业的游戏本地化翻译，将日文 RPG 游戏文本翻译成简体中文。规则：\n"
    "1. `⟦CCn⟧` 形式的占位符是不可翻译、不可移动位置、不可增删的控制码标记，必须原样保留，"
    "两侧不要增删空格。\n"
    "2. 保持角色对话的语气和人称，不要过度意译。\n"
    "3. 术语表里出现的词必须使用给定的固定译名。\n"
    "4. 只输出译文本身，不要输出解释、引号或任何多余内容。"
    "{glossary_section}"
)

# 一次请求最多打包多少条不同原文一起翻译。几万行文本量级下，一行一请求在时间和 token 成本
# 上都不现实（每次请求都要重复付一遍 system prompt 的 token），打包成批次是主要的省钱手段，
# 配合 DeepSeek 的 context caching（system prompt 不变，能吃到缓存折扣）。
DEFAULT_BATCH_SIZE = 25

_BATCH_INSTRUCTION = (
    "请把下面编号的文本逐条翻译成简体中文。每条译文必须以 [编号] 开头另起一行，"
    "编号要和输入的编号一一对应，不要合并、不要跳号、不要输出编号以外的任何解释性文字。"
)
_ITEM_MARKER_RE = re.compile(r"^\[(\d+)\]\s*", re.MULTILINE)


def _build_system_prompt(glossary: dict[str, str]) -> str:
    if not glossary:
        return _TRANSLATE_SYSTEM_PROMPT_TEMPLATE.format(glossary_section="")
    lines = "\n".join(f"- {term} -> {translation}" for term, translation in glossary.items())
    glossary_section = f"\n\n术语表（必须使用以下固定译名）：\n{lines}"
    return _TRANSLATE_SYSTEM_PROMPT_TEMPLATE.format(glossary_section=glossary_section)


class _Job(NamedTuple):
    source_text: str
    group: list[TextUnit]
    protected_text: str
    mapping: dict[str, str]
    context: str


def _build_single_user_prompt(protected_text: str, context: str) -> str:
    if context:
        return f"上下文：\n{context}\n\n待翻译文本：\n{protected_text}"
    return f"待翻译文本：\n{protected_text}"


def _build_batch_user_prompt(items: list[_Job]) -> str:
    parts = [_BATCH_INSTRUCTION]
    for i, job in enumerate(items, start=1):
        if job.context:
            parts.append(f"[{i}] 上下文：{job.context}\n待翻译：{job.protected_text}")
        else:
            parts.append(f"[{i}] 待翻译：{job.protected_text}")
    return "\n\n".join(parts)


def _parse_batch_response(response: str, expected_count: int) -> dict[int, str] | None:
    matches = list(_ITEM_MARKER_RE.finditer(response))
    if len(matches) != expected_count:
        return None

    result: dict[int, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(response)
        try:
            index = int(m.group(1))
        except ValueError:
            return None
        result[index] = response[start:end].strip()

    if set(result.keys()) != set(range(1, expected_count + 1)):
        return None
    return result


async def translate_units(
    client: LLMClient,
    store: Store,
    units: list[TextUnit],
    glossary: dict[str, str],
    concurrency: int = 4,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cancel_check: Callable[[], bool] | None = None,
) -> None:
    """按 source_text 去重分组，相同原文只调用一次 LLM；多个不同分组打包进同一次请求
    （见 batch_size），减少大文本量下的请求数和重复的 system prompt token 开销。

    on_progress(completed, total) 在每个去重分组翻译完成后调用一次，供 GUI 显示
    "翻译批次 X/Y" 进度用（见 spec 第 10 节），不传则跳过。

    cancel_check() 在每个批次真正发请求前检查一次：返回 True 就跳过这个批次，不发起
    新的 API 调用（对应用户点了"停止"或者软件被要求中断）。已经在缓存命中路径写完的
    结果、以及调用这个函数之前就已经在途的批次不受影响——已经花出去的 token 不浪费，
    只是不再新增。
    """
    system_prompt = _build_system_prompt(glossary)
    semaphore = asyncio.Semaphore(concurrency)

    groups: dict[str, list[TextUnit]] = {}
    for unit in units:
        if unit.status != "pending":
            continue
        groups.setdefault(unit.source_text, []).append(unit)

    jobs: list[_Job] = []
    cache_hits: list[tuple[list[TextUnit], str]] = []
    for source_text, group in groups.items():
        cached = store.get_memory(compute_source_hash(source_text))
        if cached is not None:
            cache_hits.append((group, cached))
        else:
            protected_text, mapping = protect(source_text)
            jobs.append(_Job(source_text, group, protected_text, mapping, group[0].context))

    total = len(jobs) + len(cache_hits)
    completed = 0

    def _write_result(group: list[TextUnit], translated_text: str) -> None:
        nonlocal completed
        for unit in group:
            store.update_translation(unit.id, translated_text, status="translated")
        completed += 1
        if on_progress is not None:
            on_progress(completed, total)

    for group, cached in cache_hits:
        _write_result(group, cached)

    def _cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    async def _translate_single_job(job: _Job) -> None:
        user_prompt = _build_single_user_prompt(job.protected_text, job.context)
        async with semaphore:
            # 取消检查放在拿到并发名额之后、真正发请求之前：还在排队等名额的批次，
            # 轮到它的时候如果已经被取消就直接放弃，不发这次请求；但已经拿到名额、
            # 正在等待响应的调用不会被这个检查打断，等它自然跑完并落盘。
            if _cancelled():
                return
            raw = await client.chat(system_prompt, user_prompt)
        translated_text = restore(raw.strip(), job.mapping)
        store.set_memory(compute_source_hash(job.source_text), job.source_text, translated_text)
        _write_result(job.group, translated_text)

    async def _translate_batch(batch: list[_Job]) -> None:
        if len(batch) == 1:
            await _translate_single_job(batch[0])
            return

        user_prompt = _build_batch_user_prompt(batch)
        async with semaphore:
            if _cancelled():
                return
            raw = await client.chat(system_prompt, user_prompt)

        parsed = _parse_batch_response(raw, len(batch))
        if parsed is None:
            # 模型没按格式回，退化成逐条调用，保证正确性（牺牲这一批的省 token 收益）
            await asyncio.gather(*(_translate_single_job(job) for job in batch))
            return

        for i, job in enumerate(batch, start=1):
            translated_text = restore(parsed[i], job.mapping)
            store.set_memory(compute_source_hash(job.source_text), job.source_text, translated_text)
            _write_result(job.group, translated_text)

    batches = [jobs[i : i + batch_size] for i in range(0, len(jobs), batch_size)]
    await asyncio.gather(*(_translate_batch(batch) for batch in batches))
