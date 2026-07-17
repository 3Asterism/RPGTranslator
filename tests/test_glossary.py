from __future__ import annotations

import pytest

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.core.ir import TextUnit
from rpg_translator.translate.glossary import extract_glossary_candidates, parse_glossary_response
from rpg_translator.translate.llm_client import LLMClient, LLMConfig


def test_parse_glossary_response_plain_json():
    response = '[{"term": "ハロルド", "translation": "哈罗德"}, {"term": "村", "translation": "村庄"}]'
    assert parse_glossary_response(response) == {"ハロルド": "哈罗德", "村": "村庄"}


def test_parse_glossary_response_wrapped_in_markdown_fence():
    response = '```json\n[{"term": "ハロルド", "translation": "哈罗德"}]\n```'
    assert parse_glossary_response(response) == {"ハロルド": "哈罗德"}


def test_parse_glossary_response_bare_fence_no_language_tag():
    response = '```\n[{"term": "村", "translation": "村庄"}]\n```'
    assert parse_glossary_response(response) == {"村": "村庄"}


def test_parse_glossary_response_empty_array():
    assert parse_glossary_response("[]") == {}


def test_parse_glossary_response_malformed_json_returns_empty():
    assert parse_glossary_response("这不是 JSON，是模型瞎说的一段话") == {}


def test_parse_glossary_response_non_list_json_returns_empty():
    assert parse_glossary_response('{"term": "ハロルド", "translation": "哈罗德"}') == {}


def test_parse_glossary_response_skips_malformed_items():
    response = '[{"term": "村"}, {"translation": "缺term"}, {"term": "湖", "translation": "湖"}]'
    assert parse_glossary_response(response) == {"湖": "湖"}


def _make_unit(source_text: str, uid: str) -> TextUnit:
    return TextUnit(
        id=uid,
        engine="mz",
        file_path="data/Map001.json",
        locator=f"events/1/pages/0/list/{uid}/parameters/0",
        context="",
        source_text=source_text,
    )


@pytest.mark.anyio
async def test_extract_glossary_candidates_against_real_provider():
    api_key = get_deepseek_api_key()
    if not api_key:
        pytest.skip("本地未配置 DEEPSEEK_API_KEY，跳过真实 API 调用测试")

    settings = Settings()
    units = [
        _make_unit("ハロルドさん、この村へようこそ。", "1"),
        _make_unit("ハロルドは村の鍛冶屋です。", "2"),
        _make_unit("村の外は危険です。", "3"),
    ]
    config = LLMConfig(
        api_key=api_key, base_url=settings.deepseek_base_url, model=settings.deepseek_model
    )
    async with LLMClient(config) as client:
        candidates = await extract_glossary_candidates(client, units)

    assert isinstance(candidates, dict)
    assert any("ハロルド" in term for term in candidates)


@pytest.mark.anyio
async def test_extract_glossary_candidates_empty_units_skips_api_call():
    config = LLMConfig(api_key="unused", base_url="https://example.invalid", model="unused")
    async with LLMClient(config) as client:
        candidates = await extract_glossary_candidates(client, [])
    assert candidates == {}
