from __future__ import annotations

import json

from rpg_translator.core.ir import TextUnit
from rpg_translator.translate.llm_client import LLMClient

_GLOSSARY_SYSTEM_PROMPT = (
    "你是一个游戏文本术语抽取助手。给定一批游戏原文，找出其中反复出现、需要统一译名的"
    "人名、地名、专有名词，给出建议的中文译名。只输出一个 JSON 数组，不要有任何其他文字、"
    '不要用 markdown 代码块包裹，每个元素形如 {"term": "原文术语", "translation": "建议译名"}。'
    "如果没有值得记录的术语，输出空数组 []。"
)


async def extract_glossary_candidates(
    client: LLMClient, units: list[TextUnit], sample_limit: int = 200
) -> dict[str, str]:
    unique_texts = list(dict.fromkeys(u.source_text for u in units))[:sample_limit]
    if not unique_texts:
        return {}
    user_prompt = "\n".join(unique_texts)
    response = await client.chat(_GLOSSARY_SYSTEM_PROMPT, user_prompt)
    return parse_glossary_response(response)


def parse_glossary_response(response: str) -> dict[str, str]:
    text = response.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}

    if not isinstance(data, list):
        return {}

    result: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        term = item.get("term")
        translation = item.get("translation")
        if isinstance(term, str) and isinstance(translation, str) and term.strip():
            result[term] = translation
    return result
