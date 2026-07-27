from __future__ import annotations

import re

# 元/百万 tokens，(输入单价, 输出单价)。数据来自服务商价格页人工查证，仅供预估——
# 服务商价格随时可能调整，实际扣费以官网/账单为准，不是精确计费。
#
# key 统一按"去掉厂商前缀（`厂商/型号` 里 `/` 前面那截，比如 deepseek-ai/、Qwen/）
# + 小写"匹配（见 estimate_cost_cny），不用为同一型号在有无前缀两种写法各存一份——
# 旧版本这里是 "deepseek-ai/deepseek-v4-flash" 和 "deepseek-v4-flash" 各存一条，
# 但用户在设置里选的型号名（settings_dialog._MODELS）跟这里的 key 十有八九对不上
# （前缀有没有、大小写、"qwen3.5-27b" vs "Qwen/Qwen3.5-27B" 这类），导致费用预估
# 实际上无论选哪个型号大概率都命中不了、直接显示不出费用——这是用户反馈"这个功能
# 几乎是废的"的根本原因，不是价格本身不准。
_PRICING_PER_MILLION_CNY: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (1.0, 2.0),
    "deepseek-v4-pro": (12.0, 24.0),
    "deepseek-v3.2": (2.0, 3.0),
    "qwen-plus": (0.8, 4.8),
    "qwen3.6-flash": (1.2, 7.2),
    "qwen3.7-plus": (2.0, 8.0),
    "qwen3.5-35b-a3b": (0.4, 3.2),
    "qwen3.5-27b": (0.6, 4.8),
}

# 阿里百炼这类云服务商经常把型号滚动发布成带日期的快照名（比如设置里的
# "qwen-plus-2025-07-28"），没法为每个快照各存一条记录——按"去掉日期后缀之后
# 是不是等于表里已有的型号家族名"做一次前缀匹配，命中就沿用那个家族的价格，
# 比直接判定为"型号未收录"更贴近实际情况。
_DATED_SNAPSHOT_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")

# 表里查不到、去日期后缀也匹配不上的型号（比如下拉框里还没收录的新型号、用户在
# 可编辑下拉框里自己手填的自定义型号名）：旧版本这种情况直接返回 None，UI 上等于
# 完全不显示费用——但实际上多数在用的型号都会走到这条路径（见上面的说明），效果
# 等同于"这个功能几乎从来没起作用过"。这里改成按主流中文云端模型的输入/输出单价
# 给一个数量级上的粗略估算（不对应任何具体型号，纯参考），调用方通过返回的第二个
# 值知道这是不是精确匹配，在 UI 上标"仅供参考"而不是假装是精确账单。
_FALLBACK_RATE_CNY = (2.0, 6.0)


def _lookup_rates(model: str) -> tuple[tuple[float, float], bool]:
    key = model.strip().lower().rsplit("/", 1)[-1]
    rates = _PRICING_PER_MILLION_CNY.get(key)
    if rates is None:
        rates = _PRICING_PER_MILLION_CNY.get(_DATED_SNAPSHOT_SUFFIX_RE.sub("", key))
    if rates is not None:
        return rates, True
    return _FALLBACK_RATE_CNY, False


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> tuple[float, bool]:
    """按型号估算这次调用花了多少人民币。返回 (预估花费, 是否精确匹配到型号定价)：
    第二个值为 False 时，花费是按 _FALLBACK_RATE_CNY 粗略估算的通用参考值，不是
    这个型号的真实单价，调用方应该在 UI 上标"估算/仅供参考"而不是当成精确账单。"""
    (input_rate, output_rate), exact = _lookup_rates(model)
    cost = prompt_tokens / 1_000_000 * input_rate + completion_tokens / 1_000_000 * output_rate
    return cost, exact
