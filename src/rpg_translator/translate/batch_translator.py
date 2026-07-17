from __future__ import annotations

import asyncio

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


def _build_system_prompt(glossary: dict[str, str]) -> str:
    if not glossary:
        return _TRANSLATE_SYSTEM_PROMPT_TEMPLATE.format(glossary_section="")
    lines = "\n".join(f"- {term} -> {translation}" for term, translation in glossary.items())
    glossary_section = f"\n\n术语表（必须使用以下固定译名）：\n{lines}"
    return _TRANSLATE_SYSTEM_PROMPT_TEMPLATE.format(glossary_section=glossary_section)


async def translate_units(
    client: LLMClient,
    store: Store,
    units: list[TextUnit],
    glossary: dict[str, str],
    concurrency: int = 4,
) -> None:
    """按 source_text 去重分组，相同原文只调用一次 LLM，结果写入翻译记忆表复用。"""
    system_prompt = _build_system_prompt(glossary)
    semaphore = asyncio.Semaphore(concurrency)

    groups: dict[str, list[TextUnit]] = {}
    for unit in units:
        if unit.status != "pending":
            continue
        groups.setdefault(unit.source_text, []).append(unit)

    async def _translate_group(source_text: str, group: list[TextUnit]) -> None:
        source_hash = compute_source_hash(source_text)
        cached = store.get_memory(source_hash)
        if cached is not None:
            translated_text = cached
        else:
            protected_text, mapping = protect(source_text)
            representative = group[0]
            if representative.context:
                user_prompt = f"上下文：\n{representative.context}\n\n待翻译文本：\n{protected_text}"
            else:
                user_prompt = f"待翻译文本：\n{protected_text}"

            async with semaphore:
                raw_translation = await client.chat(system_prompt, user_prompt)
            translated_text = restore(raw_translation.strip(), mapping)
            store.set_memory(source_hash, source_text, translated_text)

        for unit in group:
            store.update_translation(unit.id, translated_text, status="translated")

    await asyncio.gather(*(_translate_group(text, group) for text, group in groups.items()))
