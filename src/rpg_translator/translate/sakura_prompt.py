from __future__ import annotations

from rpg_translator.translate.batch_translator import Job, PromptStrategy

# SakuraLLM/GalTransl 官方 prompt 模板（来自 GalTransl 项目 Backend/Prompts.py 的
# GalTransl_SYSTEM_PROMPT / GalTransl_TRANS_PROMPT_V3，Sakura 系列模型是照着这套
# 固定模板微调的，不是通用大模型——直接套用 DeepSeek 那套自由格式的 prompt
# （batch_translator.py 的默认策略）效果会打折扣，见调研记录。
SAKURA_SYSTEM_PROMPT = (
    "你是一个视觉小说翻译模型，可以通顺地使用给定的术语表以指定的风格将日文翻译成简体中文，"
    "并联系上下文正确使用人称代词，注意不要混淆使役态和被动态的主语和宾语，不要擅自添加原文中"
    "没有的特殊符号，也不要擅自增加或减少换行。"
)

_TRANS_TEMPLATE = (
    "[History]"
    "参考以下术语表（可为空，格式为src->dst #备注）：\n"
    "[Glossary]\n"
    "根据以上术语表的对应关系和备注，结合历史剧情和上下文，将下面的文本从日文翻译成简体中文：\n"
    "[Input]"
)


def _escape_newlines(text: str) -> str:
    # 官方协议按"一行一条"严格对齐输入输出行数（见 _parse_batch_response），源文本
    # 里真正的换行符会破坏这个对齐，所以转成字面 "\n" 两个字符，回填时再还原。
    return text.replace("\r\n", "\\n").replace("\n", "\\n")


def _build_history(items: list[Job]) -> str:
    # 官方实现里 [History] 放的是"上一批的翻译结果"，用来串联多轮请求之间的剧情
    # 连续性；我们的 Job.context 是"同一事件分组里的其它原文台词"（见
    # batch_translator.translate_units 的分组逻辑），语义上更接近"当前剧情背景"而
    # 非"上一次翻译结果"，但同样是给模型的剧情上下文，借用 [History] 这个槽位承载。
    #
    # 一个批次可能打包了来自不同事件（不同上下文）的条目——[History] 是整批共享
    # 的一个槽位，没法按条目区分，硬取第一条的 context 会把不相关的剧情背景安到
    # 其它条目头上。批次内 context 不一致时宁可不给背景，也不要给错的。
    contexts = {job.context for job in items}
    if len(contexts) != 1:
        return ""
    context = next(iter(contexts))
    if not context:
        return ""
    return f"历史剧情：{context}\n"


def _build_single_prompt(protected_text: str, context: str) -> str:
    history = f"历史剧情：{context}\n" if context else ""
    prompt = _TRANS_TEMPLATE.replace("[History]", history)
    prompt = prompt.replace("[Glossary]", "")
    return prompt.replace("[Input]", _escape_newlines(protected_text))


def _build_batch_prompt(items: list[Job]) -> str:
    prompt = _TRANS_TEMPLATE.replace("[History]", _build_history(items))
    prompt = prompt.replace("[Glossary]", "")
    lines = [_escape_newlines(job.protected_text) for job in items]
    return prompt.replace("[Input]", "\n".join(lines))


def _parse_batch_response(response: str, expected_count: int) -> dict[int, str] | None:
    # 官方协议不认识 [编号] 标签，靠"第 N 行输出对应第 N 行输入"严格按行对齐——行数
    # 一旦对不上（模型合并/拆分/多输出了几行）就直接判失败，交给上层退化成逐条重问，
    # 这比标签解析更简单，但也意味着一旦模型夹带解释文字导致多出几行就会整批报废。
    lines = response.strip("\n").split("\n")
    if len(lines) != expected_count:
        return None
    return {i: line.replace("\\n", "\n") for i, line in enumerate(lines, start=1)}


SAKURA_PROMPT_STRATEGY = PromptStrategy(
    system_prompt=SAKURA_SYSTEM_PROMPT,
    build_single_prompt=_build_single_prompt,
    build_batch_prompt=_build_batch_prompt,
    parse_batch_response=_parse_batch_response,
    # 关掉 ⟦CCn⟧ 占位符包装：实测这套量化过的本地小模型对生僻 unicode 占位符的
    # 保留很不稳定（单条约 4~6 成概率抄错/加乱码），但对 RPG Maker 原生的
    # \C[1] 这类反斜杠控制码保留非常稳（8/8 次原样保留）——直接让控制码原样
    # 透传给模型，见 batch_translator.PromptStrategy.wrap_control_codes 的说明。
    wrap_control_codes=False,
)
