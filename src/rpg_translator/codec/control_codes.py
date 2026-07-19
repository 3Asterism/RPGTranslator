from __future__ import annotations

import re

CONTROL_CODE_PATTERN = re.compile(r"\\[A-Za-z]+(\[[^\]]*\])?|\\[.^!|]")

# 部分工程会用 "\n<角色名>" 在消息开头标出说话人（实测某个真实 RPG Maker MV 工程的
# 文本里就是这么写的，\n 是普通换行控制码，尖括号是插件/作者自己的说话人标记约定，
# 不属于标准控制码语法）。\n 本身已经被 CONTROL_CODE_PATTERN 当控制码保护，但裸露的
# 尖括号模型拿不准该不该保留，实测会不稳定地把整个 "<角色名>" 一起吞掉。这里只在
# "\n" 后面紧跟 "<...>" 时，额外保护这两个尖括号本身，中间的名字仍然暴露给模型正常
# 翻译/音译——不能把整个 "<角色名>" 当不透明控制码整体占位，那样名字就永远译不出来了
# （比如"ローズ"应该译成"罗丝"，实测这个也确实在正常工作的情况下发生过）。
_SPEAKER_TAG_PATTERN = re.compile(r"(⟦CC\d+⟧)<([^>\n]*)>")


def protect(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}

    def repl(m: re.Match[str]) -> str:
        token = f"⟦CC{len(mapping)}⟧"
        mapping[token] = m.group(0)
        return token

    protected = CONTROL_CODE_PATTERN.sub(repl, text)

    def speaker_repl(m: re.Match[str]) -> str:
        newline_token, name = m.group(1), m.group(2)
        if mapping.get(newline_token) != "\\n":
            return m.group(0)  # 前面那个占位符不是 \n（比如是 \C[1] 之类），不是说话人标记，不动
        open_token = f"⟦CC{len(mapping)}⟧"
        mapping[open_token] = "<"
        close_token = f"⟦CC{len(mapping)}⟧"
        mapping[close_token] = ">"
        return f"{newline_token}{open_token}{name}{close_token}"

    return _SPEAKER_TAG_PATTERN.sub(speaker_repl, protected), mapping


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
