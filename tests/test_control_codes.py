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


def test_protect_keeps_speaker_name_translatable_inside_newline_angle_bracket_tag():
    """真实 RPG Maker MV 工程实测过的写法：\\n<角色名> 在消息开头标出说话人，尖括号
    本身要保护住（不然模型有时会连名字带括号一起吞掉），但括号里的名字要留给模型
    正常翻译/音译，不能把整段当不透明控制码——那样名字就永远译不出来了。"""
    text = "\\n<ローズ>ふふ・・・♥"
    protected, mapping = protect(text)

    assert "<" not in protected and ">" not in protected  # 尖括号本身被占位符替换掉了
    assert "ローズ" in protected  # 名字仍然是可翻译的明文，没被占位符吞掉
    assert restore(protected, mapping) == text

    # 模拟模型正常翻译了名字、原样保留了占位符
    translated_by_llm = protected.replace("ローズ", "罗丝").replace("ふふ・・・", "呵呵・・・")
    assert restore(translated_by_llm, mapping) == "\\n<罗丝>呵呵・・・♥"


def test_protect_does_not_treat_angle_brackets_after_non_newline_code_as_speaker_tag():
    """尖括号紧跟在别的控制码（不是 \\n）后面时，不该被当成说话人标记去拆开保护——
    这只是这个游戏里 \\n<角色名> 这一种具体写法的针对性修复，不是"任何控制码后面
    跟尖括号都当说话人标签"的通用规则。"""
    text = r"\C[1]<foo>"
    protected, mapping = protect(text)
    assert "<foo>" in protected  # 尖括号原样保留在明文里，没被额外拆分保护
    assert restore(protected, mapping) == text
