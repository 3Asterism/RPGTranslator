from __future__ import annotations

import re

# 用私有区字符（U+E000/U+E001）包裹 token 编号——这两个码点属于 Unicode 私有
# 使用区，游戏原文/译文正常情况下不会出现，冲突概率可忽略。
_TOKEN_OPEN = ""
_TOKEN_CLOSE = ""
_TOKEN_RE = re.compile(f"{_TOKEN_OPEN}(\\d+){_TOKEN_CLOSE}")

# 第一版只做通用兜底，覆盖最常见的四类：TMP 富文本标签、花括号占位符、printf
# 风格格式化符、转义换行。未覆盖到的游戏特定标记允许译文里偶尔漏保护，后续按
# 实际反馈补规则。用 re.VERBOSE + 具名分组便于以后扩展新规则时看清楚每条规则
# 各自匹配什么，不是无差别囫囵一个大正则。
_COMBINED_RE = re.compile(
    r"""
    (?P<tag></?[a-zA-Z][^<>]*>)      # TMP 富文本标签：<color=#fff> </b> 等
  | (?P<brace>\{[^{}]*\})             # {0} / {player_name}
  | (?P<printf>%\d*\$?[sdif])         # %s / %d / %1$s
  | (?P<newline>\\n)                  # 转义换行
    """,
    re.VERBOSE,
)


def protect(text: str) -> tuple[str, list[str]]:
    """把文本里能识别的占位符依次替换成 <私有区>索引<私有区> token，返回替换后
    的文本和按出现顺序记录的原始片段列表，供 restore() 还原。"""
    tokens: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"{_TOKEN_OPEN}{len(tokens) - 1}{_TOKEN_CLOSE}"

    protected = _COMBINED_RE.sub(_replace, text)
    return protected, tokens


def restore(text: str, tokens: list[str]) -> str:
    """把 protect() 生成的 token 换回原始片段。译文里出现编号超出 tokens 范围
    的 token（正常不应发生，防御 LLM 输出异常）时原样保留该 token 文本，不
    抛异常——保底之下最多是这个占位符看着有点怪，不该让整条翻译请求失败。"""

    def _replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(tokens):
            return match.group(0)
        return tokens[index]

    return _TOKEN_RE.sub(_replace, text)
