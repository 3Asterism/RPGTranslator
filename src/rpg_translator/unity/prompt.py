from __future__ import annotations

# 不复用 translate/batch_translator.py 的 _TRANSLATE_SYSTEM_PROMPT——那份连
# "在线默认"策略都绑死了 RPG Maker 的 ⟦CCn⟧ 控制码占位符约定和"人名对照"批量
# 结构，不是通用日译中/英译中 prompt。这里单条、无历史、无术语表（shim 无
# 状态），只约束"保留占位符 token 原样、只输出译文"这类通用规则。
SYSTEM_PROMPT = (
    "你是专业的游戏本地化翻译。规则：\n"
    "1. 形如 N 的标记（N 是数字）是占位符，代表游戏原有的格式标签"
    "或变量，必须原样保留、不可翻译、不可移动、不可增删，两侧不加空格。\n"
    "2. 只输出译文本身，不要解释、引号或多余内容。\n"
    "3. 保留原文语气，不过度意译，不擅自增删换行。"
)


def build_user_prompt(protected_text: str, source_lang: str, target_lang: str) -> str:
    return f"将下面的文本从 {source_lang} 翻译成 {target_lang}：\n{protected_text}"
