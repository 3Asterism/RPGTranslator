from __future__ import annotations

import re
from typing import Callable

CONTROL_CODE_PATTERN = re.compile(r"\\[A-Za-z]+(\[[^\]]*\])?|\\[.^!|]|\r\n|\n")
# 末尾 \r\n|\n 分支：数据库 note/description 这类字段常见的真实换行符（不是游戏
# 引擎自己的反斜杠转义控制码，是 source_text 字符串里字面的 \x0A/\x0D\x0A）。这类
# 字段没有像消息文本那样按行拆成独立的 TextUnit（见 engines/_rgss_common.py 的
# rewrap_paragraph 说明），换行就是内容本身携带的分段信息——不保护的话它会连同普通
# 文字一起被喂给模型，模型翻译时经常会把原本分开的段落揉成一整段连续文字（LLM
# 翻译不逐字保留输入的空白/换行是常见行为），回填进游戏后表现为原本该分行显示的
# 文字全挤在一行里、字符叠在一起。跟其它控制码一样走 protect()/⟦CCn⟧ 占位符机制，
# 让模型只需要"原样保留一个不透明占位符"而不是"自己判断该不该保留这个换行"。
# \r\n 分支排在 \n 前面，保证一次匹配掉完整的 \r\n 两个字符，不会拆成"孤立的 \r
# （不匹配，原样留在明文里）+ 被保护的 \n"这种不干净的结果。

# 部分工程会用 "\n<角色名>" 在消息开头标出说话人（实测某个真实 RPG Maker MV 工程的
# 文本里就是这么写的，\n 是普通换行控制码，尖括号是插件/作者自己的说话人标记约定，
# 不属于标准控制码语法）。\n 本身已经被 CONTROL_CODE_PATTERN 当控制码保护，但裸露的
# 尖括号模型拿不准该不该保留，实测会不稳定地把整个 "<角色名>" 一起吞掉。这里只在
# "\n" 后面紧跟 "<...>" 时，额外保护这两个尖括号本身，中间的名字仍然暴露给模型正常
# 翻译/音译——不能把整个 "<角色名>" 当不透明控制码整体占位，那样名字就永远译不出来了
# （比如"ローズ"应该译成"罗丝"，实测这个也确实在正常工作的情况下发生过）。
_SPEAKER_TAG_PATTERN = re.compile(r"(⟦CC\d+⟧)<([^>\n]*)>")

# 部分工程会用 "\n<角色名>" 在消息开头标出说话人（实测某个真实 RPG Maker MV 工程的
# 文本里就是这么写的，\n 是普通换行控制码，尖括号是插件/作者自己的说话人标记约定，
# 不属于标准控制码语法）。\n 本身已经被 CONTROL_CODE_PATTERN 当控制码保护，但裸露的
# 尖括号模型拿不准该不该保留，实测会不稳定地把整个 "<角色名>" 一起吞掉。这里只在
# "\n" 后面紧跟 "<...>" 时，额外保护这两个尖括号本身，中间的名字仍然暴露给模型正常
# 翻译/音译——不能把整个 "<角色名>" 当不透明控制码整体占位，那样名字就永远译不出来了
# （比如"ローズ"应该译成"罗丝"，实测这个也确实在正常工作的情况下发生过）。
_SPEAKER_TAG_PATTERN = re.compile(r"(⟦CC\d+⟧)<([^>\n]*)>")


def protect(text: str, token_fn: Callable[[int], str] | None = None) -> tuple[str, dict[str, str]]:
    """token_fn 可以换掉默认的 ⟦CCn⟧ 占位符格式——2026-08 实测 Sakura-GalTransl-14B
    对"整行几乎就是一个被 \\C[n]...\\C[0] 包住的短专有名词"这类行，不管标记长什么样
    （原始反斜杠码、⟦CCn⟧ 占位符）都有约定俗成的丢弃倾向（会把标记当装饰符号一并
    吞掉，或者换成书名号这类它自己学到的等价物），但对完全不带括号/标点的纯字母
    数字 token（比如 QCC00）保留得很稳（同一批测试里 33/33 次完整保留）。见
    sakura_prompt.py 的 SAKURA_PROMPT_STRATEGY_14B。"""
    if token_fn is None:
        token_fn = lambda n: f"⟦CC{n}⟧"  # noqa: E731
    mapping: dict[str, str] = {}

    def repl(m: re.Match[str]) -> str:
        token = token_fn(len(mapping))
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


def bare_alnum_token(n: int) -> str:
    """给 protect() 用的无括号占位符格式：QCC00/QCC01/…，固定 2 位数字补零——
    保证任何一个 token 都不会是另一个 token 的字符串前缀（QCC1 不补零的话会是
    QCC10 的前缀），从根上避免 restore() 替换时互相污染，不用依赖替换顺序兜底。
    2 位数字上限 100 个不同控制码/行，正常游戏文本不会有一行踩到这个上限。"""
    return f"QCC{n:02d}"


def restore(text: str, mapping: dict[str, str]) -> str:
    # 按 token 长度从长到短替换：不带括号分隔符的 token（见 protect 的 token_fn）
    # 短序号可能是长序号的字符串前缀（比如 "QCC1" 是 "QCC10" 的前缀），先替换短的
    # 会把长 token 从中间截断污染掉；⟦CCn⟧ 这种带括号的格式本来就不会有这个前缀
    # 冲突，但排序不影响它的正确性，统一按这个顺序处理更省心。
    for token, code in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(token, code)
    return text


def extract_codes(text: str) -> list[str]:
    """找出文本里出现过哪些控制码（去重），不做任何替换——给"不经过 protect() 占位符
    包装、原始控制码直接透传给模型"的场景用（见 sakura_prompt.py）：不需要 protect()
    的占位符映射，但仍然需要知道该校验哪些码的存在，才能判断模型是不是老实把控制码
    保留下来了。"""
    return list(dict.fromkeys(m.group(0) for m in CONTROL_CODE_PATTERN.finditer(text)))
