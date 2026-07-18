from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.core.ir import TextUnit
from rpg_translator.core.store import Store
from rpg_translator.translate.batch_translator import _parse_batch_response, translate_units
from rpg_translator.translate.llm_client import LLMClient, LLMConfig


def test_parse_batch_response_well_formed():
    response = "[1] 你好\n\n[2] 再见"
    assert _parse_batch_response(response, 2) == {1: "你好", 2: "再见"}


def test_parse_batch_response_preserves_multiline_translation():
    response = "[1] 第一行\n第二行\n\n[2] 单行"
    result = _parse_batch_response(response, 2)
    assert result[1] == "第一行\n第二行"
    assert result[2] == "单行"


def test_parse_batch_response_wrong_count_returns_none():
    response = "[1] 你好"
    assert _parse_batch_response(response, 2) is None


def test_parse_batch_response_missing_index_returns_none():
    response = "[1] 你好\n\n[3] 再见"  # 跳号，没有 [2]
    assert _parse_batch_response(response, 2) is None


def test_parse_batch_response_no_markers_returns_none():
    assert _parse_batch_response("完全不按格式回复的一段话", 3) is None


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


class _BatchAwareStub:
    """老老实实按 [编号] 格式回复的模型：把每条 "待翻译：xxx" 里的 xxx 原样回填成
    "[N] 译文:xxx"，用来验证批量打包请求 -> 按编号解析回填 这条主路径。批次里只剩
    一条时 translate_units 会走单条快速路径（不带编号），这里也一并兼容。"""

    def __init__(self):
        self.call_count = 0
        self.last_user_prompt = ""

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        import re

        self.call_count += 1
        self.last_user_prompt = user_prompt
        items = re.findall(r"\[(\d+)\].*?待翻译：(.*?)(?=\n\n\[\d+\]|\Z)", user_prompt, re.S)
        if items:
            lines = [f"[{n}] 译文:{text.strip()}" for n, text in items]
            return "\n".join(lines)

        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        return f"译文:{user_prompt[idx:].strip()}"


class _MalformedBatchStub:
    """故意不按 [编号] 格式回复整段话，逼出 fallback 逐条重试路径。"""

    def __init__(self):
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        return "这是一段不符合格式要求的胡乱回复"


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
        # batch_size=1：这个测试关注的是断点续跑本身，不是批量打包，避免批量解析
        # 失败走 fallback 时多出的一次"打包尝试"调用把调用计数搅浑
        await translate_units(stub, store, [units[0]], glossary={}, concurrency=4, batch_size=1)
        assert stub.call_count == 1
        assert store.get_unit("1").status == "translated"

        # 第二轮：重新执行，对全部三条 units 再跑一次 pipeline
        # （store.list_units() 里 unit 1 现在已经是 status=translated）
        all_units_from_store = store.list_units()
        await translate_units(
            stub, store, all_units_from_store, glossary={}, concurrency=4, batch_size=1
        )

        # 只有 2、3 是新调用的，1 不应该被重复调用
        assert stub.call_count == 1 + 2
        assert store.get_unit("2").status == "translated"
        assert store.get_unit("3").status == "translated"


@pytest.mark.anyio
async def test_translate_units_respects_concurrency_limit(tmp_path: Path):
    stub = _ConcurrencyTrackingStub(delay=0.05)
    with Store(tmp_path / "units.db") as store:
        # 8 个互不相同的 source_text，batch_size=1 强制拆成 8 次独立请求（不然会被
        # 打包进同一批次，只发一次请求，就测不出并发限流是否真的生效了）
        units = [_make_unit(str(i), f"文本{i}") for i in range(8)]
        store.upsert_units(units)

        await translate_units(stub, store, units, glossary={}, concurrency=3, batch_size=1)

        assert stub.max_in_flight == 3  # 确实顶到了限流上限，不是巧合地没超过


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

        # batch_size=1：这个测试关注的是去重分组本身，不是批量打包
        await translate_units(stub, store, units, glossary={}, concurrency=4, batch_size=1)

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


class _DropsPlaceholderOnceStub:
    """第一次调用把 protect() 打的所有 ⟦CCn⟧ 占位符漏掉（复现小模型实测出现的丢控制
    码问题），之后老实原样回填——用来验证漏占位符会被判定为失败并触发自动重试，
    而不是把缺了控制码的残缺译文原样落盘。"""

    def __init__(self):
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        protected_text = user_prompt[idx:]
        if self.call_count == 1:
            return re.sub(r"⟦CC\d+⟧", "", protected_text)
        return f"TRANSLATED[{protected_text}]"


@pytest.mark.anyio
async def test_translate_units_retries_when_control_code_placeholder_dropped(tmp_path: Path):
    stub = _DropsPlaceholderOnceStub()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "\\C[1]勇者よ")
        store.upsert_units([unit])

        failures = await translate_units(
            stub, store, [unit], glossary={}, concurrency=4, retry_wait_seconds=0
        )

        assert failures == []
        result = store.get_unit("1")
        assert result.status == "translated"
        assert "\\C[1]" in result.translated_text
        assert stub.call_count == 2  # 第一次丢占位符判失败，自动重试轮救回来


class _BatchDropsOnePlaceholderStub:
    """批量打包请求按 [编号] 格式正常回复，但其中一条把控制码占位符漏掉——用来验证
    只有那一条会退化成单独调用重问，其它已经解析正确的条目不用跟着重来。"""

    def __init__(self, bad_index: int):
        self.bad_index = bad_index
        self.batch_calls = 0
        self.single_calls = 0

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        items = re.findall(r"\[(\d+)\].*?待翻译：(.*?)(?=\n\n\[\d+\]|\Z)", user_prompt, re.S)
        if items:
            self.batch_calls += 1
            lines = []
            for n, text in items:
                text = text.strip()
                if int(n) == self.bad_index:
                    text = re.sub(r"⟦CC\d+⟧", "", text)
                lines.append(f"[{n}] 译文:{text}")
            return "\n".join(lines)

        self.single_calls += 1
        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        return f"译文:{user_prompt[idx:].strip()}"


@pytest.mark.anyio
async def test_translate_units_batch_item_placeholder_dropped_falls_back_to_single_call(
    tmp_path: Path,
):
    stub = _BatchDropsOnePlaceholderStub(bad_index=2)
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "文本1号"),
            _make_unit("2", "\\C[1]文本2号"),
            _make_unit("3", "文本3号"),
        ]
        store.upsert_units(units)

        failures = await translate_units(
            stub, store, units, glossary={}, concurrency=4, batch_size=25
        )

        assert failures == []
        assert stub.batch_calls == 1
        assert stub.single_calls == 1  # 只有第 2 条退化成单独调用
        assert store.get_unit("1").translated_text == "译文:文本1号"
        assert "\\C[1]" in store.get_unit("2").translated_text
        assert "⟦CC" not in store.get_unit("2").translated_text
        assert store.get_unit("3").translated_text == "译文:文本3号"


@pytest.mark.anyio
async def test_translate_units_includes_glossary_in_system_prompt(tmp_path: Path):
    stub = _StubClient("你好")
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])

        await translate_units(stub, store, [unit], glossary={"ハロルド": "哈罗德"}, concurrency=4)

        assert "ハロルド -> 哈罗德" in stub.calls[0][0]


@pytest.mark.anyio
async def test_translate_units_batches_multiple_jobs_into_one_request(tmp_path: Path):
    stub = _BatchAwareStub()
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}号") for i in range(5)]
        store.upsert_units(units)

        await translate_units(stub, store, units, glossary={}, concurrency=4, batch_size=25)

        assert stub.call_count == 1  # 5 条全部打包进了一次请求
        for i in range(5):
            assert store.get_unit(str(i)).translated_text == f"译文:文本{i}号"


@pytest.mark.anyio
async def test_translate_units_splits_into_multiple_batches_when_exceeding_batch_size(
    tmp_path: Path,
):
    stub = _BatchAwareStub()
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}号") for i in range(5)]
        store.upsert_units(units)

        await translate_units(stub, store, units, glossary={}, concurrency=4, batch_size=2)

        # 5 条，batch_size=2 -> 3 批（2+2+1）
        assert stub.call_count == 3
        for i in range(5):
            assert store.get_unit(str(i)).translated_text == f"译文:文本{i}号"


@pytest.mark.anyio
async def test_translate_units_falls_back_to_individual_calls_when_batch_parse_fails(
    tmp_path: Path,
):
    stub = _MalformedBatchStub()
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}号") for i in range(3)]
        store.upsert_units(units)

        await translate_units(stub, store, units, glossary={}, concurrency=4, batch_size=25)

        # 1 次打包尝试（解析失败）+ 3 次逐条重试 = 4 次调用
        assert stub.call_count == 4
        for i in range(3):
            result = store.get_unit(str(i))
            assert result.status == "translated"
            assert result.translated_text == "这是一段不符合格式要求的胡乱回复"


class _FlakyStub:
    """含有特定片段的请求永远报错（模拟内容审核拒绝、或所有 provider 都失败后抛出的
    不可重试错误），其余请求正常返回，用来验证一批里有问题的那一条不会拖累其它条目。"""

    def __init__(self, bad_snippet: str):
        self.bad_snippet = bad_snippet
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        if self.bad_snippet in user_prompt:
            raise RuntimeError("400 Bad Request: data_inspection_failed")
        return "翻译结果"


@pytest.mark.anyio
async def test_translate_units_skips_failing_job_without_aborting_batch(tmp_path: Path):
    """回归测试：真实项目里翻译一个含 R18 内容的游戏时，备用 provider（阿里云百炼）会对
    其中一条文本返回 400 data_inspection_failed（内容审核拒绝），这是不可重试的错误。
    修复前 asyncio.gather 会让这一条的异常直接冒泡，取消掉同批乃至其它并发批次里正在跑的
    翻译，导致整个"开始翻译"直接报错退出。修复后应该只跳过这一条（保留 pending 供下次
    重跑续译），其余条目正常翻译成功。"""
    stub = _FlakyStub("违禁内容")
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "正常文本A"),
            _make_unit("2", "包含违禁内容的句子"),
            _make_unit("3", "正常文本B"),
        ]
        store.upsert_units(units)

        failures = await translate_units(
            stub, store, units, glossary={}, concurrency=4, batch_size=25
        )

        assert store.get_unit("1").status == "translated"
        assert store.get_unit("3").status == "translated"
        assert store.get_unit("2").status == "pending"  # 失败条目保留待译，不影响其它条目
        assert len(failures) == 1
        assert failures[0][0] == "包含违禁内容的句子"


class _FlakyThenSucceedsStub:
    """前几次调用报错、之后成功——模拟"当时所有 provider 都在限流/抖动，
    过几秒会恢复"的场景，用来验证自动重试轮确实能把最终失败的条目救回来。"""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise RuntimeError("503 Service Unavailable")
        return "翻译结果"


@pytest.mark.anyio
async def test_translate_units_auto_retries_transient_failure_until_success(tmp_path: Path):
    stub = _FlakyThenSucceedsStub(fail_times=1)
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])

        failures = await translate_units(
            stub, store, [unit], glossary={}, concurrency=4, batch_size=1,
            retry_wait_seconds=0,
        )

        assert failures == []
        assert store.get_unit("1").status == "translated"
        assert stub.call_count == 2  # 第一轮失败 + 自动重试第一轮成功


@pytest.mark.anyio
async def test_translate_units_auto_retry_does_not_double_count_progress(tmp_path: Path):
    """重试轮成功/失败都不应该让 completed 超过 total——不然 GUI 进度条会跑飞。"""
    stub = _FlakyThenSucceedsStub(fail_times=1)
    progress_calls: list[tuple[int, int]] = []
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])

        await translate_units(
            stub, store, [unit], glossary={}, concurrency=4, batch_size=1,
            retry_wait_seconds=0,
            on_progress=lambda done, total: progress_calls.append((done, total)),
        )

        assert progress_calls == [(1, 1)]  # 重试轮成功不再重复触发 on_progress


@pytest.mark.anyio
async def test_translate_units_auto_retry_exhausted_still_fails(tmp_path: Path):
    stub = _FlakyStub("こんにちは")  # 永远失败，模拟内容审核拒绝这类不可恢复的错误
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])

        failures = await translate_units(
            stub, store, [unit], glossary={}, concurrency=4, batch_size=1,
            retry_wait_seconds=0, auto_retry_rounds=2,
        )

        assert len(failures) == 1
        assert store.get_unit("1").status == "pending"
        assert stub.call_count == 3  # 1 次初始 + 2 轮自动重试，全部失败


@pytest.mark.anyio
async def test_translate_units_cancel_stops_auto_retry(tmp_path: Path):
    """点了停止之后，自动重试轮不应该再发起新请求，也不应该傻等满重试间隔。"""
    stub = _FlakyStub("こんにちは")
    cancelled = False

    def _cancel_check() -> bool:
        return cancelled

    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])

        task = asyncio.create_task(
            translate_units(
                stub, store, [unit], glossary={}, concurrency=4, batch_size=1,
                retry_wait_seconds=10,  # 故意设大：真的被打断而不是等满才会快速返回
                cancel_check=_cancel_check,
            )
        )
        for _ in range(200):
            if stub.call_count >= 1:
                break
            await asyncio.sleep(0.01)
        assert stub.call_count == 1
        cancelled = True

        failures = await asyncio.wait_for(task, timeout=2.0)

        assert stub.call_count == 1  # 没有发起自动重试的请求
        assert len(failures) == 1  # 第一轮的失败仍如实报告
        assert store.get_unit("1").status == "pending"


@pytest.mark.anyio
async def test_translate_units_cancel_stops_new_batches_and_aborts_in_flight(
    tmp_path: Path,
):
    """模拟点了"停止"按钮：cancel_check() 从某一刻起一直返回 True。

    真实场景里（高并发 + 大 batch_size）一次请求可能覆盖几十条文本、耗时数秒到数十秒，
    如果"停止"只挡新请求、放任已经在等响应的请求自然跑完，用户会感觉点了停止但还在
    持续烧 token——所以已经过了并发闸门、正在等 API 响应的请求也要被主动打断（cancel
    掉底层调用），不是傻等它跑完。被打断的条目保留 status="pending"，下次重跑续译，
    不计入失败。用并发限流卡住 2 个请求先真正发出去，取消标记在它们卡在半路时才置位，
    验证：1) 排队中的另外 4 个批次不会被发出去；2) 已经在等响应的 2 个也被打断，不会
    落盘成翻译结果。"""
    release = asyncio.Event()

    class _GatedStub:
        def __init__(self):
            self.call_count = 0

        async def chat(self, system_prompt: str, user_prompt: str) -> str:
            self.call_count += 1
            await release.wait()
            return "翻译结果"

    stub = _GatedStub()
    cancelled = False

    def _cancel_check() -> bool:
        return cancelled

    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}") for i in range(6)]
        store.upsert_units(units)

        task = asyncio.create_task(
            translate_units(
                stub,
                store,
                units,
                glossary={},
                concurrency=2,
                batch_size=1,
                cancel_check=_cancel_check,
            )
        )
        # 等已经拿到并发名额的 2 个请求真正发出去（call_count 到 2 后它们卡在
        # release.wait() 上），这时候再置位取消
        for _ in range(100):
            if stub.call_count >= 2:
                break
            await asyncio.sleep(0.01)
        assert stub.call_count == 2

        cancelled = True
        await asyncio.wait_for(task, timeout=2.0)
        release.set()  # 收尾：让还卡在 release.wait() 上、已被 cancel 的协程能正常退出

        assert stub.call_count == 2  # 排队中的 4 个批次没有被发出去
        translated = [u for u in store.list_units() if u.status == "translated"]
        assert len(translated) == 0  # 在途的 2 个也被主动打断，没有一个落盘成功
        pending = [u for u in store.list_units() if u.status == "pending"]
        assert len(pending) == 6  # 全部保留 pending，可以下次重跑续译


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


@pytest.mark.anyio
async def test_translate_units_batching_works_against_real_provider(tmp_path: Path):
    """关键验证：真实模型是否真的会按 [编号] 格式回复多条打包请求，而不是每次都
    退化成 fallback 逐条重试——如果退化了，批量省 token/省请求数这件事就是空话。"""
    api_key = get_deepseek_api_key()
    if not api_key:
        pytest.skip("本地未配置 DEEPSEEK_API_KEY，跳过真实 API 调用测试")

    settings = Settings()
    texts = [
        "こんにちは、旅人よ。",
        "この村へようこそ。",
        "\\C[1]勇者よ、目を覚ませ。",
        "気をつけて行ってらっしゃい。",
        "宝箱を見つけた！",
    ]
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), text) for i, text in enumerate(texts)]
        store.upsert_units(units)

        config = LLMConfig(
            api_key=api_key, base_url=settings.deepseek_base_url, model=settings.deepseek_model
        )
        async with LLMClient(config) as client:
            call_count = 0
            original_chat = client.chat

            async def _counting_chat(system_prompt: str, user_prompt: str) -> str:
                nonlocal call_count
                call_count += 1
                return await original_chat(system_prompt, user_prompt)

            client.chat = _counting_chat
            await translate_units(client, store, units, glossary={}, concurrency=4, batch_size=10)

        assert call_count == 1, (
            f"期望 5 条一次性打包成 1 次请求，实际调用了 {call_count} 次"
            "（说明真实模型没有按 [编号] 格式回复，退化成了逐条 fallback）"
        )
        for i in range(5):
            result = store.get_unit(str(i))
            assert result.status == "translated"
            assert result.translated_text
        control_code_unit = store.get_unit("2")
        assert "\\C[1]" in control_code_unit.translated_text
        assert "⟦CC" not in control_code_unit.translated_text
