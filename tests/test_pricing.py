from __future__ import annotations

from rpg_translator.translate.pricing import estimate_cost_cny


def test_estimate_cost_known_model():
    # deepseek-v4-flash: 输入 1 元/M，输出 2 元/M
    cost = estimate_cost_cny("deepseek-v4-flash", 1_000_000, 500_000)
    assert cost is not None
    assert round(cost, 4) == round(1.0 + 1.0, 4)


def test_estimate_cost_is_case_insensitive_and_strips_whitespace():
    assert estimate_cost_cny("DeepSeek-V4-Flash", 1_000_000, 0) == estimate_cost_cny(
        " deepseek-v4-flash ", 1_000_000, 0
    )


def test_estimate_cost_unknown_model_returns_none():
    assert estimate_cost_cny("some-unlisted-model", 1000, 1000) is None


def test_estimate_cost_zero_tokens_is_zero():
    assert estimate_cost_cny("deepseek-v4-flash", 0, 0) == 0.0
