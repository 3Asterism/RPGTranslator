from rpg_translator.codec.control_codes import protect, restore


def test_protect_replaces_bracketed_and_bare_codes():
    text = r"\C[1]勇者\N[1]よ、\G を手に入れた！\!"
    protected, mapping = protect(text)

    assert r"\C[1]" not in protected
    assert r"\N[1]" not in protected
    assert r"\G" not in protected
    assert r"\!" not in protected
    assert len(mapping) == 4
    for token, code in mapping.items():
        assert token in protected
        assert code in (r"\C[1]", r"\N[1]", r"\G", r"\!")


def test_protect_gives_distinct_tokens_to_same_code_repeated():
    text = r"\C[1]赤\C[1]赤"
    protected, mapping = protect(text)
    assert len(mapping) == 2
    assert protected.count("⟦CC") == 2


def test_protect_no_control_codes_leaves_text_and_mapping_empty():
    text = "こんにちは、世界。"
    protected, mapping = protect(text)
    assert protected == text
    assert mapping == {}


def test_restore_reverses_protect_exactly():
    text = r"\C[1]勇者\N[1]よ、\V[7] の力を借りよ\.\|\^"
    protected, mapping = protect(text)
    assert restore(protected, mapping) == text


def test_restore_reverses_translated_text_with_same_mapping():
    text = r"\N[1]さん、こんにちは。"
    protected, mapping = protect(text)
    # LLM 视角：占位符原样保留，只翻译普通文本部分
    translated_by_llm = protected.replace("こんにちは", "hello")
    result = restore(translated_by_llm, mapping)
    assert result == r"\N[1]さん、hello。"


def test_protect_handles_icon_and_actor_variable_codes():
    text = r"\I[24]\P[3]"
    protected, mapping = protect(text)
    assert len(mapping) == 2
    assert set(mapping.values()) == {r"\I[24]", r"\P[3]"}
