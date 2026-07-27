from __future__ import annotations

from rpg_translator.translate.pricing import estimate_cost_cny


def test_estimate_cost_known_model():
    # deepseek-v4-flash: 输入 1 元/M，输出 2 元/M
    cost, exact = estimate_cost_cny("deepseek-v4-flash", 1_000_000, 500_000)
    assert exact is True
    assert round(cost, 4) == round(1.0 + 1.0, 4)


def test_estimate_cost_is_case_insensitive_and_strips_whitespace():
    assert estimate_cost_cny("DeepSeek-V4-Flash", 1_000_000, 0) == estimate_cost_cny(
        " deepseek-v4-flash ", 1_000_000, 0
    )


def test_estimate_cost_strips_vendor_prefix():
    """设置面板里的型号名很多是 "厂商/型号" 格式（比如 SiliconFlow 的
    "Qwen/Qwen3.5-27B"），跟价目表里不带厂商前缀的 key 要能对得上——旧版本这里
    要求两种写法各存一条，实际收录的型号十有八九对不上，这是费用预估"不管选什么
    型号都显示不出来"的根本原因。"""
    exact_match, is_exact = estimate_cost_cny("Qwen/Qwen3.5-27B", 1_000_000, 1_000_000)
    plain, _ = estimate_cost_cny("qwen3.5-27b", 1_000_000, 1_000_000)
    assert is_exact is True
    assert exact_match == plain


def test_estimate_cost_matches_dated_snapshot_by_family_prefix():
    """阿里百炼这类服务商会把型号发布成带日期的快照名（比如 "qwen-plus-2025-07-28"），
    去掉日期后缀应该能匹配到价目表里的 "qwen-plus" 家族价格，而不是直接判定为
    型号未收录。"""
    dated, exact = estimate_cost_cny("qwen-plus-2025-07-28", 1_000_000, 1_000_000)
    family, _ = estimate_cost_cny("qwen-plus", 1_000_000, 1_000_000)
    assert exact is True
    assert dated == family


def test_estimate_cost_unknown_model_falls_back_to_rough_estimate_instead_of_none():
    """价目表查不到的型号（比如用户在可编辑下拉框里自己手填的自定义型号名）不该
    直接不给费用——那样等于这个功能对大多数实际在用的型号完全不起作用。应该按
    粗略均价给一个数量级参考，并且明确告知调用方这不是精确匹配。"""
    cost, exact = estimate_cost_cny("some-unlisted-model", 1_000_000, 1_000_000)
    assert exact is False
    assert cost > 0


def test_estimate_cost_zero_tokens_is_zero():
    cost, exact = estimate_cost_cny("deepseek-v4-flash", 0, 0)
    assert cost == 0.0
    assert exact is True
