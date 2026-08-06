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


def test_parse_batch_response_out_of_order_markers_returns_none():
    """按位置严格校验（第 i 个匹配到的编号必须正好是 i+1），不是只看编号集合是否
    等于 {1..N}——这个响应里 [1]/[2] 齐全但顺序颠倒，之前只按集合比较会误判成
    "合法"，把内容错配给错误的编号；现在应该判失败走二分重试。"""
    response = "[2] 再见\n\n[1] 你好"
    assert _parse_batch_response(response, 2) is None


def test_parse_batch_response_no_markers_returns_none():
    assert _parse_batch_response("完全不按格式回复的一段话", 3) is None


class _StubClient:
    def __init__(self, response: str = "MOCK_TRANSLATION"):
        self.response = response
        self.call_count = 0
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        self.call_count += 1
        self.calls.append((system_prompt, user_prompt))
        return self.response


class _EchoStub:
    """把 user_prompt 里"待翻译文本："之后的内容原样吐回来，模拟一个乖乖保留占位符的模型。"""

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
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

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
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

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        self.call_count += 1
        return "这是一段不符合格式要求的胡乱回复"


class _RecordingEchoStub:
    """把 "待翻译文本：" 之后的内容套一层 "译:" 前缀原样吐回来，同时记下每次请求
    实际发给模型的 user_prompt——用来断言"\\n<角色名>正文 拆分之后模型的 prompt
    里到底出现过什么"，而不是只看最终写回结果对不对。"""

    def __init__(self):
        self.call_count = 0
        self.prompts: list[str] = []

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        self.call_count += 1
        self.prompts.append(user_prompt)
        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        return f"译:{user_prompt[idx:]}"


class _DropsAngleBracketPlaceholderStub:
    """模拟真实观察到的失败模式：只要回复里出现跟"尖括号"相关的占位符，就把它删掉
    （复现"模型偶尔把 <角色名> 连括号带名字一起吞掉"的行为）。用来验证：\\n<角色名>
    正文 经过拆分之后，模型的 prompt 里压根不会出现跟尖括号相关的占位符，这种"会
    吞占位符"的模型也不会触发失败/重试——因为它根本没机会吞。"""

    def __init__(self):
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        self.call_count += 1
        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        protected_text = user_prompt[idx:]
        translated = f"译:{protected_text}"
        # 复现失败行为：吞掉所有占位符 token（不管它对应的原始码是什么）
        return re.sub(r"⟦CC\d+⟧", "", translated)


def _make_unit(
    uid: str,
    source_text: str,
    status: str = "pending",
    context: str = "",
    context_group: str = "",
) -> TextUnit:
    return TextUnit(
        id=uid,
        engine="mz",
        file_path="data/Map001.json",
        locator=f"events/1/pages/0/list/{uid}/parameters/0",
        context=context,
        context_group=context_group,
        source_text=source_text,
        status=status,
    )


class _ConcurrencyTrackingStub:
    """记录同时在跑的请求数峰值，用来验证信号量并发限流是否真的生效。"""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
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
        await translate_units(stub, store, [units[0]], concurrency=4, batch_size=1)
        assert stub.call_count == 1
        assert store.get_unit("1").status == "translated"

        # 第二轮：重新执行，对全部三条 units 再跑一次 pipeline
        # （store.list_units() 里 unit 1 现在已经是 status=translated）
        all_units_from_store = store.list_units()
        await translate_units(
            stub, store, all_units_from_store, concurrency=4, batch_size=1
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

        await translate_units(stub, store, units, concurrency=3, batch_size=1)

        assert stub.max_in_flight == 3  # 确实顶到了限流上限，不是巧合地没超过


@pytest.mark.anyio
async def test_translate_units_reports_progress_via_callback(tmp_path: Path):
    stub = _StubClient("翻译结果")
    progress_calls: list[tuple[int, int]] = []
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}") for i in range(3)]
        store.upsert_units(units)

        await translate_units(
            stub, store, units, concurrency=2,
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
        await translate_units(stub, store, units, concurrency=4, batch_size=1)

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

        await translate_units(stub, store, [reviewed], concurrency=4)

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

        await translate_units(stub, store, [unit], concurrency=4)

        assert stub.call_count == 0
        assert store.get_unit("1").translated_text == "缓存里的翻译"


@pytest.mark.anyio
async def test_translate_units_restores_control_codes(tmp_path: Path):
    stub = _EchoStub()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "\\C[1]勇者よ")
        store.upsert_units([unit])

        await translate_units(stub, store, [unit], concurrency=4)

        result = store.get_unit("1")
        assert "\\C[1]" in result.translated_text
        assert "⟦CC" not in result.translated_text


@pytest.mark.anyio
async def test_translate_units_restores_embedded_real_newlines(tmp_path: Path):
    r"""回归测试：数据库 description/note 这类字段常见的真实换行符（字面的 \x0A，
    不是 \\n 反斜杠转义控制码）之前没有被 protect() 保护，模型翻译多段文字时经常
    不老实保留原始换行/分段结构，导致本该分行显示的内容被揉成一整段回填进游戏
    （表现为"字符堆叠在一起、不换行"）。"""
    stub = _EchoStub()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "第一段。\n第二段，换了话题。")
        store.upsert_units([unit])

        await translate_units(stub, store, [unit], concurrency=4)

        result = store.get_unit("1")
        assert "\n" in result.translated_text
        assert "⟦CC" not in result.translated_text


class _DropsPlaceholderOnceStub:
    """第一次调用把 protect() 打的所有 ⟦CCn⟧ 占位符漏掉（复现小模型实测出现的丢控制
    码问题），之后老实原样回填——用来验证漏占位符会被判定为失败并触发自动重试，
    而不是把缺了控制码的残缺译文原样落盘。"""

    def __init__(self):
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
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
            stub, store, [unit], concurrency=4, retry_wait_seconds=0
        )

        assert failures == []
        result = store.get_unit("1")
        assert result.status == "translated"
        assert "\\C[1]" in result.translated_text
        assert stub.call_count == 2  # 第一次丢占位符判失败，自动重试轮救回来


class _LeaksContextOnceStub:
    """复现 A/B 测试发现的真实事故：模型第一次回复没有老实只翻「待翻译」那一句，而是
    把「上下文」也整段翻译/复述进了回复里，真正的译文被埋在最后——用来验证这种
    跑题回复会被判定为失败并触发自动重试，而不是把一大段不相关内容原样落盘。"""

    def __init__(self):
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        self.call_count += 1
        marker = "待翻译文本（只翻译并只输出这一句）：\n"
        idx = user_prompt.index(marker) + len(marker)
        protected_text = user_prompt[idx:]
        if self.call_count == 1:
            return (
                "村长：欢迎来到这个村子，一路上辛苦了吧。\n"
                "旅人：谢谢款待，这里的风景真美。\n"
                "村长：前面那栋就是新搬来的邻居家了。\n"
                "待翻译：" + protected_text
            )
        return protected_text


@pytest.mark.anyio
async def test_translate_units_retries_when_translation_leaks_context(tmp_path: Path):
    stub = _LeaksContextOnceStub()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit(
            "1", "わあ、これがイー・ジャンが引っ越す家なんだ", context="村の入り口での会話"
        )
        store.upsert_units([unit])

        failures = await translate_units(
            stub, store, [unit], concurrency=4, retry_wait_seconds=0
        )

        assert failures == []
        result = store.get_unit("1")
        assert result.status == "translated"
        assert result.translated_text == "わあ、これがイー・ジャンが引っ越す家なんだ"
        assert stub.call_count == 2  # 第一次夹带上下文判失败，自动重试轮救回来


class _RunawayLengthOnceStub:
    """第一次回复不含"上下文/待翻译"这类标签字样，但长度远超原文——用来验证纯粹的
    长度异常信号（不依赖标签关键词）也能被识别为跑题回复。"""

    def __init__(self):
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        self.call_count += 1
        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        protected_text = user_prompt[idx:]
        if self.call_count == 1:
            return "毫不相关的大段无关文字" * 10
        return f"译文:{protected_text}"


@pytest.mark.anyio
async def test_translate_units_retries_when_translation_is_abnormally_long(tmp_path: Path):
    stub = _RunawayLengthOnceStub()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])

        failures = await translate_units(
            stub, store, [unit], concurrency=4, retry_wait_seconds=0
        )

        assert failures == []
        result = store.get_unit("1")
        assert result.status == "translated"
        assert result.translated_text == "译文:こんにちは"
        assert stub.call_count == 2  # 第一次长度异常判失败，自动重试轮救回来


class _BatchLeaksContextOnceStub:
    """批量打包请求按 [编号] 格式正常回复，但其中一条夹带了上下文内容——用来验证
    只有那一条会退化成单独调用重问，其它已经解析正确的条目不用跟着重来。"""

    def __init__(self, bad_index: int):
        self.bad_index = bad_index
        self.batch_calls = 0
        self.single_calls = 0

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        items = re.findall(r"\[(\d+)\].*?待翻译：(.*?)(?=\n\n\[\d+\]|\Z)", user_prompt, re.S)
        if items:
            self.batch_calls += 1
            lines = []
            for n, text in items:
                text = text.strip()
                if int(n) == self.bad_index:
                    text = "村长：欢迎来到这个村子。\n旅人：谢谢款待。\n待翻译：" + text
                lines.append(f"[{n}] 译文:{text}")
            return "\n".join(lines)

        self.single_calls += 1
        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        return f"译文:{user_prompt[idx:].strip()}"


@pytest.mark.anyio
async def test_translate_units_batch_item_leaks_context_falls_back_to_single_call(
    tmp_path: Path,
):
    stub = _BatchLeaksContextOnceStub(bad_index=2)
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "文本1号"),
            _make_unit("2", "文本2号"),
            _make_unit("3", "文本3号"),
        ]
        store.upsert_units(units)

        failures = await translate_units(
            stub, store, units, concurrency=4, batch_size=25
        )

        assert failures == []
        assert stub.batch_calls == 1
        assert stub.single_calls == 1  # 只有第 2 条退化成单独调用
        assert store.get_unit("1").translated_text == "译文:文本1号"
        assert store.get_unit("2").translated_text == "译文:文本2号"
        assert store.get_unit("3").translated_text == "译文:文本3号"


class _BatchDropsOnePlaceholderStub:
    """批量打包请求按 [编号] 格式正常回复，但其中一条把控制码占位符漏掉——用来验证
    只有那一条会退化成单独调用重问，其它已经解析正确的条目不用跟着重来。"""

    def __init__(self, bad_index: int):
        self.bad_index = bad_index
        self.batch_calls = 0
        self.single_calls = 0

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
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
            stub, store, units, concurrency=4, batch_size=25
        )

        assert failures == []
        assert stub.batch_calls == 1
        assert stub.single_calls == 1  # 只有第 2 条退化成单独调用
        assert store.get_unit("1").translated_text == "译文:文本1号"
        assert "\\C[1]" in store.get_unit("2").translated_text
        assert "⟦CC" not in store.get_unit("2").translated_text
        assert store.get_unit("3").translated_text == "译文:文本3号"




@pytest.mark.anyio
async def test_translate_units_batches_multiple_jobs_into_one_request(tmp_path: Path):
    stub = _BatchAwareStub()
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}号") for i in range(5)]
        store.upsert_units(units)

        await translate_units(stub, store, units, concurrency=4, batch_size=25)

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

        await translate_units(stub, store, units, concurrency=4, batch_size=2)

        # 5 条，batch_size=2 -> 3 批（2+2+1）
        assert stub.call_count == 3
        for i in range(5):
            assert store.get_unit(str(i)).translated_text == f"译文:文本{i}号"


@pytest.mark.anyio
async def test_translate_units_splits_batch_at_context_group_boundary(tmp_path: Path):
    """同一个 context_group（比如同一个事件页面）的条目要尽量分进同一批、当成一整段
    翻译；不同 context_group 就算凑不满 batch_size 也要另起一批，不能把不相关场景的
    台词混进同一次请求（段落进段落出，调研见 CLAUDE.md）。"""
    stub = _BatchAwareStub()
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "文本1号", context_group="page-A"),
            _make_unit("2", "文本2号", context_group="page-A"),
            _make_unit("3", "文本3号", context_group="page-B"),
        ]
        store.upsert_units(units)

        await translate_units(stub, store, units, concurrency=4, batch_size=25)

        # page-A 的 2 条打包成 1 次请求，page-B 单独 1 条另起一次请求，一共 2 次调用
        # ——不是把 3 条不分场景硬凑成 1 次请求
        assert stub.call_count == 2
        for i in range(1, 4):
            assert store.get_unit(str(i)).translated_text == f"译文:文本{i}号"


@pytest.mark.anyio
async def test_translate_units_falls_back_to_individual_calls_when_batch_parse_fails(
    tmp_path: Path,
):
    stub = _MalformedBatchStub()
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}号") for i in range(3)]
        store.upsert_units(units)

        await translate_units(stub, store, units, concurrency=4, batch_size=25)

        # _bisect_batch 是对半递归二分，不是直接拆成 len(batch) 次单条：3 条先整批
        # 试一次（失败）-> 二分成 1+2 -> 1 条那半直接单条重试成功，2 条那半又先按
        # 批尝试一次（这个 stub 不管批大小回复都不合规，所以还是失败）-> 再二分成
        # 1+1 各自单条重试成功。合计 1(整批) + 1(单条) + 1(2 条批) + 2(单条) = 5 次。
        assert stub.call_count == 5
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

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
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
            stub, store, units, concurrency=4, batch_size=25
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

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
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
            stub, store, [unit], concurrency=4, batch_size=1,
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
            stub, store, [unit], concurrency=4, batch_size=1,
            retry_wait_seconds=0,
            on_progress=lambda done, total: progress_calls.append((done, total)),
        )

        assert progress_calls == [(1, 1)]  # 重试轮成功不再重复触发 on_progress


class _FailTwiceThenSucceedForBareTextStub:
    """对某个特定的裸文本（protected_text 精确等于 bare_text，没有上下文）前
    fail_times 次调用报错、之后成功；其它内容（比如角色名翻译请求）永远直接成功。
    用来复现"两个不同 Job 恰好有相同的 source_text，但属于不同的 (source_text,
    result_prefix) 分组"这种碰撞场景——两个 Job 会发起两次内容完全相同的请求。"""

    def __init__(self, bare_text: str, fail_times: int):
        self.bare_text = bare_text
        self.fail_times = fail_times
        self.bare_call_count = 0
        self.call_count = 0

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        self.call_count += 1
        marker = "待翻译文本：\n"
        idx = user_prompt.index(marker) + len(marker)
        protected_text = user_prompt[idx:]
        if protected_text == self.bare_text:
            self.bare_call_count += 1
            if self.bare_call_count <= self.fail_times:
                raise RuntimeError("503 Service Unavailable")
        return f"译:{protected_text}"


@pytest.mark.anyio
async def test_translate_units_retries_both_jobs_when_source_text_collides_across_groups(
    tmp_path: Path,
):
    r"""回归测试：\n<角色名>正文 拆分出来的"正文"部分，可能跟另一条独立台词的原文
    字面相同（比如都是"……"这种很短的常见台词）——此时两个 Job 的 source_text 相同，
    但属于不同的 (source_text, result_prefix) 分组，理应被当成两次独立的翻译任务。
    自动重试轮之前是按 source_text 反查 Job 对象再重新提交，两个 Job 会互相覆盖，
    其中一个从此再也不会被重新提交，也不会出现在最终返回的 failures 里——注入阶段
    又会用原文兜底，表现为这一条译文悄悄留成了日文原文，却没有任何失败提示。
    修复后两个 Job 应该都被独立重试成功。"""
    stub = _FailTwiceThenSucceedForBareTextStub(bare_text="X", fail_times=2)
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("plain", "X"),
            _make_unit("tagged", "\\n<Name>X"),
        ]
        store.upsert_units(units)

        failures = await translate_units(
            stub, store, units, concurrency=4, batch_size=1, retry_wait_seconds=0
        )

        assert failures == []
        assert store.get_unit("plain").status == "translated"
        assert store.get_unit("tagged").status == "translated"
        assert store.get_unit("plain").translated_text == "译:X"
        assert store.get_unit("tagged").translated_text == "\\n<译:Name>译:X"


@pytest.mark.anyio
async def test_translate_units_auto_retry_exhausted_still_fails(tmp_path: Path):
    stub = _FlakyStub("こんにちは")  # 永远失败，模拟内容审核拒绝这类不可恢复的错误
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "こんにちは")
        store.upsert_units([unit])

        failures = await translate_units(
            stub, store, [unit], concurrency=4, batch_size=1,
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
                stub, store, [unit], concurrency=4, batch_size=1,
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

        async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
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
            await translate_units(client, store, [unit], concurrency=2)

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

            async def _counting_chat(
                system_prompt: str, user_prompt: str, extra_body: dict | None = None
            ) -> str:
                nonlocal call_count
                call_count += 1
                return await original_chat(system_prompt, user_prompt, extra_body)

            client.chat = _counting_chat
            await translate_units(client, store, units, concurrency=4, batch_size=10)

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


@pytest.mark.anyio
async def test_translate_units_splits_speaker_tag_name_and_body_never_exposing_brackets(
    tmp_path: Path,
):
    r"""真实工程实测到的写法：\n<角色名>正文 在消息开头标出说话人。这次改造的目标是
    不让模型看到跟尖括号相关的任何占位符/字符——直接断言发给模型的 prompt 里没有
    "<" ">"⟦CC"这几种东西，而不是只看最终结果对不对（结果对但过程里还是暴露了占
    位符的话，只是运气好没被吞，不代表这个问题真的解决了）。"""
    stub = _RecordingEchoStub()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "\\n<ローズ>ふふ・・・♥")
        store.upsert_units([unit])

        failures = await translate_units(stub, store, [unit], concurrency=4)

        assert failures == []
        for prompt in stub.prompts:
            assert "<" not in prompt
            assert ">" not in prompt
            assert "⟦CC" not in prompt

        result = store.get_unit("1")
        assert result.status == "translated"
        # 名字和正文分别过了一遍 _RecordingEchoStub 的 "译:" 前缀，代码自己拼回
        # "\n<...>...." 的结构
        assert result.translated_text == "\\n<译:ローズ>译:ふふ・・・♥"


@pytest.mark.anyio
async def test_translate_units_speaker_tag_name_translated_once_and_reused(tmp_path: Path):
    """同一个角色名在很多条台词里反复出现（说话人标签）——名字应该只真正调用一次
    模型翻译，其余全靠翻译记忆库复用，不应该每条台词都重新翻一遍这同一个名字。"""
    stub = _RecordingEchoStub()
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "\\n<ローズ>おはよう"),
            _make_unit("2", "\\n<ローズ>こんばんは"),
            _make_unit("3", "\\n<ローズ>さようなら"),
        ]
        store.upsert_units(units)

        failures = await translate_units(stub, store, units, concurrency=4)

        assert failures == []
        name_calls = [p for p in stub.prompts if p.endswith("ローズ")]
        assert len(name_calls) == 1, (
            f"角色名应该只真正调用一次模型翻译、其余靠记忆库复用，实际对名字发起了 "
            f"{len(name_calls)} 次请求"
        )
        for uid, body in (("1", "おはよう"), ("2", "こんばんは"), ("3", "さようなら")):
            assert store.get_unit(uid).translated_text == f"\\n<译:ローズ>译:{body}"


@pytest.mark.anyio
async def test_translate_units_hints_known_character_name_mentioned_inline(tmp_path: Path):
    """角色名先通过 "\\n<角色名>正文" 标签翻译过一次之后，如果正文里（不是作为说话人
    标签，而是被别的角色提到）再次出现同一个角色名，应该在 prompt 里收到统一译名的
    提示，不能让模型对同一个角色每次都重新音译一遍。"""
    stub = _RecordingEchoStub()
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "\\n<ローズ>おはよう"),
            _make_unit("2", "ローズ、待って"),  # 正文里提到同一个角色名，不是说话人标签
        ]
        store.upsert_units(units)

        failures = await translate_units(stub, store, units, concurrency=4, batch_size=1)

        assert failures == []
        hinted_prompts = [p for p in stub.prompts if "人名对照" in p]
        assert len(hinted_prompts) == 1
        assert "ローズ→译:ローズ" in hinted_prompts[0]


@pytest.mark.anyio
async def test_translate_units_does_not_hint_single_character_names(tmp_path: Path):
    """角色名长度低于 2 的不参与"正文里提及"的子串匹配提示——单字名字很容易在毫不
    相关的词里凑巧当子串命中，命中后反而可能误导模型把无关文字也套上这个角色的
    译名（见 _MIN_NAME_HINT_LENGTH 的说明）。"""
    stub = _RecordingEchoStub()
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "\\n<葵>おはよう"),
            _make_unit("2", "葵の日記帳"),  # 恰好含有单字角色名的无关词
        ]
        store.upsert_units(units)

        failures = await translate_units(stub, store, units, concurrency=4, batch_size=1)

        assert failures == []
        assert not any("人名对照" in p for p in stub.prompts)


@pytest.mark.anyio
async def test_translate_units_speaker_tag_survives_model_that_drops_placeholders(
    tmp_path: Path,
):
    r"""核心验证目标：这次改造是为了减少"占位符被模型吞掉 -> 判失败 -> 自动重试"
    这种返工。用一个专门吞占位符的对抗性 stub（复现真实观察到的失败模式）来翻译
    \n<角色名>正文，因为拆分之后这部分内容压根不含占位符，这个"会吞占位符"的
    模型也翻译得干干净净、一次就成，不会触发失败/重试。"""
    stub = _DropsAngleBracketPlaceholderStub()
    with Store(tmp_path / "units.db") as store:
        unit = _make_unit("1", "\\n<シャーロット>じ・・・\\.ちゃ？")
        store.upsert_units([unit])

        failures = await translate_units(
            stub, store, [unit], concurrency=4, retry_wait_seconds=0
        )

        # \.（等待控制码）仍然走占位符机制、仍然可能被这个对抗性 stub 吞掉、仍然
        # 可能触发失败重试——这次改造要解决的是"角色名标签"这一类，不是全部控制码。
        # 这里只断言角色名标签部分没有引入额外的失败，不强求 \. 也免疫。
        for source_text, error in failures:
            assert "シャーロット" not in error and "じ" not in error


class _WholePromptEchoStub:
    """不依赖固定 marker 切分 prompt，直接记下完整的 user_prompt、原样加前缀吐回去——
    用于只关心"发给模型的 prompt 文本里到底有没有出现某段话"的测试，不需要真的
    按具体协议格式解析。"""

    def __init__(self):
        self.call_count = 0
        self.prompts: list[str] = []

    async def chat(self, system_prompt: str, user_prompt: str, extra_body: dict | None = None) -> str:
        self.call_count += 1
        self.prompts.append(user_prompt)
        return f"译:{user_prompt}"


@pytest.mark.anyio
async def test_translate_units_speaker_name_batch_prompt_does_not_imply_dialogue_continuity(
    tmp_path: Path,
):
    r"""回归测试：多个角色名字凑不满一次单独调用、按 batch_size 打包进同一次请求时，
    之前复用跟正文台词一样的批量指令（_BATCH_INSTRUCTION）——那段指令里"如果连续
    多条编号本身就是同一段场景里的连续台词，请让人名、称呼、术语在这些条目之间
    前后保持一致"这句话，会让模型误以为一批互不相关的角色名字是同一段剧情的连续
    台词，实测出现过把某个名字直接展开翻译成一整句话（相当于把"上下文"当正文
    翻了）。这里断言发给模型翻译名字的 prompt 用的是专门的、明确说"这些名字互不
    相关"的指令，不含旧指令里"同一段场景的连续台词"这句话。"""
    stub = _WholePromptEchoStub()
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit("1", "\\n<爱丽丝>おはよう"),
            _make_unit("2", "\\n<鲍勃>こんにちは"),
        ]
        store.upsert_units(units)

        await translate_units(stub, store, units, concurrency=4, batch_size=25)

        name_prompts = [p for p in stub.prompts if "爱丽丝" in p or "鲍勃" in p]
        assert name_prompts, "没有捕获到翻译角色名的请求"
        for prompt in name_prompts:
            assert "同一段场景里的连续台词" not in prompt
            assert "互不相关" in prompt
