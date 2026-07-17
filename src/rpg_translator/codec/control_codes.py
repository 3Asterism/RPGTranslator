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
