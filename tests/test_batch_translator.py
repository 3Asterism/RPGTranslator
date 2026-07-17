from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.core.ir import TextUnit
from rpg_translator.core.store import Store
from rpg_translator.translate.batch_translator import translate_units
from rpg_translator.translate.llm_client import LLMClient, LLMConfig


class _StubClient:
    def __init__(self, response: str = "MOCK_TRANSLATION"):
        self.response = response
        self.call_count = 0
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        self.calls.append((system_prompt, user_prompt))
        return self.response


class _EchoStub:
    """把 user_prompt 里"待翻译文本："之后的内容原样吐回来，模拟一个乖乖保留占位符的模型。"""

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        protected_text = user_prompt[idx:]
        return f"TRANSLATED[{protected_text}]"


def _make_unit(uid: str, source_text: str, status: str = "pending", context: str = "") -> TextUnit:
    return TextUnit(
        id=uid,
        engine="mz",
        file_path="data/Map001.json",
        locator=f"events/1/pages/0/list/{uid}/parameters/0",
        context=context,
        source_text=source_text,
        status=status,
    )


class _ConcurrencyTrackingStub:
    """记录同时在跑的请求数峰值，用来验证信号量并发限流是否真的生效。"""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(self.delay)
        self.in_flight -= 1
        return "翻译结果"


@pytest.mark.anyio
async def test_translate_units_resume_after_interruption_skips_already_translated(
    tmp_path: Path,
):
    """模拟"翻译到一半被 kill 掉重新执行"：第一轮只翻完一部分，第二轮对全量重跑，
    已经翻译过的那部分不应该再次调用 LLM——对应 M3 断点续跑验收标准。"""
    stub = _StubClient("翻译结果")
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "こんにちは"),
            _make_unit("2", "さようなら"),
            _make_unit("3", "ありがとう"),
        ]
        store.upsert_units(units)

        # 第一轮：模拟进程只跑到第一条就被 kill 掉
        await translate_units(stub, store, [units[0]], glossary={}, concurrency=4)
        assert stub.call_count == 1
        assert store.get_unit("1").status == "translated"

        # 第二轮：重新执行，对全部三条 units 再跑一次 pipeline
        # （store.list_units() 里 unit 1 现在已经是 status=translated）
        all_units_from_store = store.list_units()
        await translate_units(stub, store, all_units_from_store, glossary={}, concurrency=4)

        # 只有 2、3 是新调用的，1 不应该被重复调用
        assert stub.call_count == 1 + 2
        assert store.get_unit("2").status == "translated"
        assert store.get_unit("3").status == "translated"


@pytest.mark.anyio
async def test_translate_units_respects_concurrency_limit(tmp_path: Path):
    stub = _ConcurrencyTrackingStub(delay=0.05)
    with Store(tmp_path / "units.db") as store:
        # 8 个互不相同的 source_text，确保会产生 8 次并发调用，而不是被去重掉
        units = [_make_unit(str(i), f"文本{i}") for i in range(8)]
        store.upsert_units(units)

        await translate_units(stub, store, units, glossary={}, concurrency=3)

        assert stub.max_in_flight <= 3


@pytest.mark.anyio
async def test_translate_units_reports_progress_via_callback(tmp_path: Path):
    stub = _StubClient("翻译结果")
    progress_calls: list[tuple[int, int]] = []
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}") for i in range(3)]
        store.upsert_units(units)

        await translate_units(
            stub, store, units, glossary={}, concurrency=2,
            on_progress=lambda done, total: progress_calls.append((done, total)),
        )

        assert len(progress_calls) == 3
        assert all(total == 3 for _, total in progress_calls)
        assert {done for done, _ in progress_calls} == {1, 2, 3}


@pytest.mark.anyio
async def test_translate_units_dedups_same_source_text_to_one_llm_call(tmp_path: Path):
    stub = _StubClient("你好")
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "こんにちは"),
            _make_unit("2", "こんにちは"),
            _make_unit("3", "さようなら"),
        ]
        store.upsert_units(units)

        await translate_units(stub, store, units, glossary={}, concurrency=4)

        assert stub.call_count == 2  # 2 个不同 source_text，各调用一次
        assert store.get_unit("1").translated_text == "你好"
        assert store.get_unit("2").translated_text == "你好"
        assert store.get_unit("1").status == "translated"


@pytest.mark.anyio
async def test_translate_units_skips_non_pending_units(tmp_path: Path):
    stub = _StubClient("不该被用到")
    with Store(tmp_path / "units.db") as store:
        reviewed = _make_unit("1", "こんにちは", status="reviewed")
        reviewed.translated_text = "已经人工确认过的翻译"
        store.upsert_units([reviewed])

        await translate_units(stub, store, [reviewed], glossary={}, concurrency=4)

        assert stub.call_count == 0
        result = store.get_unit("1")
        assert result.translated_text == "已经人工确认过的翻译"
        assert result.status == "reviewed"


@pytest.mark.anyio
async def test_translate_units_reuses_existing_translation_memory(tmp_path: Path):
    from rpg_translator.core.ir import compute_source_hash

    stub = _StubClient("不该被调用")
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])
        store.set_memory(compute_source_hash("こんにちは"), "こんにちは", "缓存里的翻译")

        await translate_units(stub, store, [unit], glossary={}, concurrency=4)

        assert stub.call_count == 0
        assert store.get_unit("1").translated_text == "缓存里的翻译"


@pytest.mark.anyio
async def test_translate_units_restores_control_codes(tmp_path: Path):
    stub = _EchoStub()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "\\C[1]勇者よ")
        store.upsert_units([unit])

        await translate_units(stub, store, [unit], glossary={}, concurrency=4)

        result = store.get_unit("1")
        assert "\\C[1]" in result.translated_text
        assert "⟦CC" not in result.translated_text


@pytest.mark.anyio
async def test_translate_units_includes_glossary_in_system_prompt(tmp_path: Path):
    stub = _StubClient("你好")
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])

        await translate_units(stub, store, [unit], glossary={"ハロルド": "哈罗德"}, concurrency=4)

        assert "ハロルド -> 哈罗德" in stub.calls[0][0]


@pytest.mark.anyio
async def test_translate_units_against_real_provider(tmp_path: Path):
    api_key = get_deepseek_api_key()
    if not api_key:
        pytest.skip("本地未配置 DEEPSEEK_API_KEY，跳过真实 API 调用测试")

    settings = Settings()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "\\C[1]こんにちは、旅人よ。", context="村の入り口での会話")
        store.upsert_units([unit])

        config = LLMConfig(
            api_key=api_key, base_url=settings.deepseek_base_url, model=settings.deepseek_model
        )
        async with LLMClient(config) as client:
            await translate_units(client, store, [unit], glossary={}, concurrency=2)

        result = store.get_unit("1")
        assert result.status == "translated"
        assert result.translated_text
        assert "\\C[1]" in result.translated_text
        assert "⟦CC" not in result.translated_text
