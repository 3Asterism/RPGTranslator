from __future__ import annotations

from rpg_translator.unity.prompt import SYSTEM_PROMPT, build_user_prompt


def test_system_prompt_mentions_placeholder_preservation_rule():
    assert "" in SYSTEM_PROMPT or "占位符" in SYSTEM_PROMPT


def test_build_user_prompt_includes_text_and_languages():
    prompt = build_user_prompt("こんにちは0", "ja", "zh-CN")
    assert "こんにちは0" in prompt
    assert "ja" in prompt
    assert "zh-CN" in prompt


def test_build_user_prompt_does_not_mutate_protected_text():
    text = "保持不变1"
    prompt = build_user_prompt(text, "en", "zh-CN")
    assert text in prompt
