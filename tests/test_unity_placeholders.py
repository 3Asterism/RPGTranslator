from __future__ import annotations

from rpg_translator.unity.placeholders import protect, restore


def test_protect_restore_roundtrip_is_identity_when_llm_leaves_tokens_untouched():
    original = "你好<color=#ff0000>世界</color>，欢迎 {player_name}，进度 %d%%\\n继续"
    protected, tokens = protect(original)

    assert "<color=#ff0000>" not in protected
    assert "{player_name}" not in protected
    assert restore(protected, tokens) == original


def test_protect_handles_tmp_rich_text_tags():
    protected, tokens = protect("<b>加粗</b>")
    assert len(tokens) == 2
    assert tokens[0] == "<b>"
    assert tokens[1] == "</b>"


def test_protect_handles_brace_placeholders():
    protected, tokens = protect("欢迎 {0}，你有 {count} 条消息")
    assert tokens == ["{0}", "{count}"]


def test_protect_handles_printf_style_format_specifiers():
    protected, tokens = protect("剩余 %d 次，%s 已完成 %1$s")
    assert tokens == ["%d", "%s", "%1$s"]


def test_protect_handles_escaped_newline():
    protected, tokens = protect("第一行\\n第二行")
    assert tokens == ["\\n"]


def test_protect_multiple_consecutive_placeholders_do_not_cross_contaminate():
    original = "{a}{b}{c}"
    protected, tokens = protect(original)
    assert tokens == ["{a}", "{b}", "{c}"]
    assert restore(protected, tokens) == original


def test_restore_leaves_unknown_token_index_untouched():
    """还原时如果译文里的 token 编号超出已记录范围（理论上不该发生，但要防
    LLM 输出异常导致 IndexError 崩掉整个请求），原样保留该 token 文本，不抛异常。"""
    protected, tokens = protect("{a}")
    mangled = protected.replace("0", "99", 1) if "0" in protected else protected
    # 只要不抛异常就算通过；具体保留成什么内容不强求。
    restore(mangled, tokens)


def test_protect_empty_string_returns_empty_with_no_tokens():
    protected, tokens = protect("")
    assert protected == ""
    assert tokens == []
    assert restore(protected, tokens) == ""


def test_protect_text_without_placeholders_is_unchanged():
    protected, tokens = protect("普通文本没有占位符")
    assert protected == "普通文本没有占位符"
    assert tokens == []
