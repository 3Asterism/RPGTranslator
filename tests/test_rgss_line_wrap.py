from __future__ import annotations

from rpg_translator.engines._rgss_common import rewrap_paragraph


def test_rewrap_pads_short_text_with_blank_lines():
    lines = rewrap_paragraph("你好", line_count=4, max_width=24)
    assert lines == ["你好", "", "", ""]


def test_rewrap_splits_at_estimated_width_for_fullwidth_chars():
    # 24 个全角字符正好占满一行（每字宽度 2 -> 24 单位），第 25 个字必须挤到下一行
    text = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯辰"
    lines = rewrap_paragraph(text, line_count=2, max_width=24)
    assert lines[0] == text[:12]
    assert lines[1] == text[12:]


def test_rewrap_mixes_ascii_and_fullwidth_widths():
    # "AB" 宽度 2 + 12 个全角字宽度 24 = 26，超过 max_width=24，全角部分要挤到下一行
    text = "AB" + "一二三四五六七八九十甲乙"
    lines = rewrap_paragraph(text, line_count=2, max_width=24)
    assert lines[0] == "AB" + "一二三四五六七八九十甲"
    assert lines[1] == "乙"


def test_rewrap_last_line_absorbs_overflow_instead_of_truncating():
    """行数不够装的时候宁可最后一行超宽，也不能丢字。"""
    text = "一" * 100
    lines = rewrap_paragraph(text, line_count=2, max_width=24)
    assert len(lines) == 2
    assert lines[0] == "一" * 12
    assert lines[1] == "一" * 88
    assert "".join(lines) == text


def test_rewrap_single_line_slot_keeps_everything_unwrapped():
    text = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸"
    lines = rewrap_paragraph(text, line_count=1, max_width=24)
    assert lines == [text]


def test_rewrap_strips_embedded_newlines_before_reflowing():
    lines = rewrap_paragraph("你好\n世界", line_count=1, max_width=24)
    assert lines == ["你好世界"]
