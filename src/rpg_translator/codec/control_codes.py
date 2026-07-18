from __future__ import annotations

import re

CONTROL_CODE_PATTERN = re.compile(r"\\[A-Za-z]+(\[[^\]]*\])?|\\[.^!|]")


def protect(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}

    def repl(m: re.Match[str]) -> str:
        token = f"⟦CC{len(mapping)}⟧"
        mapping[token] = m.group(0)
        return token

    return CONTROL_CODE_PATTERN.sub(repl, text), mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    for token, code in mapping.items():
        text = text.replace(token, code)
    return text


def extract_codes(text: str) -> list[str]:
    """找出文本里出现过哪些控制码（去重），不做任何替换——给"不经过 protect() 占位符
    包装、原始控制码直接透传给模型"的场景用（见 sakura_prompt.py）：不需要 protect()
    的占位符映射，但仍然需要知道该校验哪些码的存在，才能判断模型是不是老实把控制码
    保留下来了。"""
    return list(dict.fromkeys(m.group(0) for m in CONTROL_CODE_PATTERN.finditer(text)))
