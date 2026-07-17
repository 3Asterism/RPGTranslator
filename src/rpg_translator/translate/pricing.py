from __future__ import annotations

# 元/百万 tokens，(输入单价, 输出单价)。数据来自服务商价格页（SiliconFlow、阿里云百炼）
# 人工查证，仅供预估——服务商价格随时可能调整，实际扣费以官网/账单为准，不是精确计费。
# key 统一按小写匹配；查不到的型号不瞎猜价格，只展示 token 数、费用留空。
_PRICING_PER_MILLION_CNY: dict[str, tuple[float, float]] = {
    "deepseek-ai/deepseek-v4-flash": (1.0, 2.0),
    "deepseek-v4-flash": (1.0, 2.0),
    "deepseek-ai/deepseek-v4-pro": (12.0, 24.0),
    "deepseek-v4-pro": (12.0, 24.0),
    "qwen3.6-flash": (1.3, 7.5),
}


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """按型号估算这次调用花了多少人民币；型号不在价目表里就返回 None（调用方应该只
    展示 token 数，不要在没有价格依据的情况下瞎显示一个数字）。"""
    rates = _PRICING_PER_MILLION_CNY.get(model.strip().lower())
    if rates is None:
        return None
    input_rate, output_rate = rates
    return prompt_tokens / 1_000_000 * input_rate + completion_tokens / 1_000_000 * output_rate
