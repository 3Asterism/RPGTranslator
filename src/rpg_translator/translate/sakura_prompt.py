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
    # 连续性。batch_translator._chunk_jobs_by_group 保证一批里的条目都来自同一个
    # context_group（同一个事件页面），Job.context 对这类分组条目现在恒为""——上下文
    # 靠"同一次请求里的其它行"自然获得，不需要额外的 [History] 背景（调研见
    # CLAUDE.md：给不给这段背景对翻译质量影响不大，反而是拆成多次独立请求会让译名在
    # 请求之间漂移）。[History] 这个槽位目前只在数据库字段这类没有 context_group、
    # 仍然带静态描述性 context（比如"数据库记录：xxx"）的批次里可能派上用场——这些
    # 条目彼此本来就无关，contexts 集合大小 != 1 时直接不给背景，避免把不相关的描述
    # 安到其它条目头上。
    contexts = {job.context for job in items}
    if len(contexts) != 1:
        return ""
    context = next(iter(contexts))
    if not context:
        return ""
    return f"历史剧情：{context}\n"


def _format_glossary(name_hints: dict[str, str]) -> str:
    # GalTransl 官方术语表格式：一行一条 "src->dst #备注"（见 _TRANS_TEMPLATE 里的
    # 格式说明）。这里的条目来自 batch_translator 已经翻好的角色名表（见
    # translate_units 里从 "\n<角色名>" 说话人标签收集的 name_translations），只挑
    # 这条/这批正文里实际提到的名字（_match_name_hints），不是整份工程的角色名单，
    # 空的话原样保持模板里"可为空"的空 [Glossary] 行为不变。
    if not name_hints:
        return ""
    return "\n".join(f"{name}->{translated} #人名" for name, translated in name_hints.items())


def _build_single_prompt(protected_text: str, context: str, name_hints: dict[str, str]) -> str:
    history = f"历史剧情：{context}\n" if context else ""
    prompt = _TRANS_TEMPLATE.replace("[History]", history)
    prompt = prompt.replace("[Glossary]", _format_glossary(name_hints))
    return prompt.replace("[Input]", _escape_newlines(protected_text))


def _build_batch_prompt(items: list[Job]) -> str:
    merged_hints: dict[str, str] = {}
    for job in items:
        merged_hints.update(job.name_hints)
    prompt = _TRANS_TEMPLATE.replace("[History]", _build_history(items))
    prompt = prompt.replace("[Glossary]", _format_glossary(merged_hints))
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
    # SakuraLLM 官方推荐的采样参数（README：temperature=0.1, top_p=0.3,
    # repetition_penalty=1）。不显式传的话吃的是部署方 Modelfile 里的默认值——
    # 实测局域网测试机上部署的 sakura-galtransl:latest 的 Modelfile 里烤的是
    # temperature=0.3/top_p=0.8，比官方推荐更"热"，会增加批量翻译输出跑偏
    # （夹带上下文、行数错位）的概率，进而触发 batch_translator._bisect_batch
    # 的重试开销。这里在请求体里显式覆盖，不依赖具体部署环境有没有配对。
    extra_body={"temperature": 0.1, "top_p": 0.3},
)
