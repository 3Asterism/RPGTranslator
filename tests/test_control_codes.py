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


def test_protect_preserves_real_newlines_across_translation_round_trip():
    r"""回归测试：数据库 note/description 这类字段常见的真实换行符（字面的 \x0A，
    不是 \\n 这种反斜杠转义控制码）之前完全没被保护，直接暴露给模型翻译——模型
    翻译多段文字时经常不老实保留原始的换行/空白结构，导致本该分行显示的内容被
    揉成一整段连续文字回填进游戏，表现为"字符堆叠在一起、不换行"。"""
    text = "第一段说明。\n第二段说明，换了个话题。"
    protected, mapping = protect(text)

    assert "\n" not in protected  # 真实换行符已经被占位符替换掉
    assert len(mapping) == 1
    assert restore(protected, mapping) == text

    # 模拟模型正常翻译文字、原样保留了占位符（模型自己完全不需要判断"这个换行
    # 该不该保留"，只需要像对待其它控制码一样原样抄一遍占位符）
    translated_by_llm = protected.replace("第一段说明。", "First paragraph.").replace(
        "第二段说明，换了个话题。", "Second paragraph, different topic."
    )
    assert (
        restore(translated_by_llm, mapping)
        == "First paragraph.\nSecond paragraph, different topic."
    )


def test_protect_treats_crlf_as_a_single_token():
    text = "第一行\r\n第二行"
    protected, mapping = protect(text)
    assert "\r" not in protected and "\n" not in protected
    assert len(mapping) == 1
    assert restore(protected, mapping) == text


def test_protect_does_not_treat_angle_brackets_after_non_newline_code_as_speaker_tag():
    """尖括号紧跟在别的控制码（不是 \\n）后面时，不该被当成说话人标记去拆开保护——
    这只是这个游戏里 \\n<角色名> 这一种具体写法的针对性修复，不是"任何控制码后面
    跟尖括号都当说话人标签"的通用规则。"""
    text = r"\C[1]<foo>"
    protected, mapping = protect(text)
    assert "<foo>" in protected  # 尖括号原样保留在明文里，没被额外拆分保护
    assert restore(protected, mapping) == text
