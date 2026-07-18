from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from rpg_translator.codec.control_codes import protect
from rpg_translator.core.ir import TextUnit
from rpg_translator.core.store import Store
from rpg_translator.translate.batch_translator import Job, translate_units
from rpg_translator.translate.llm_client import LLMClient, LLMConfig
from rpg_translator.translate.sakura_prompt import (
    SAKURA_PROMPT_STRATEGY,
    SAKURA_SYSTEM_PROMPT,
    _build_batch_prompt,
    _build_single_prompt,
    _parse_batch_response,
)

# 局域网里跑 Sakura-GalTransl-7B 的适配开发/测试机（见部署记录），不是每台跑测试
# 的机器都在这个局域网里——用 TCP 可达性做门控，连不上就跳过而不是报错，
# 跟 test_batch_translator.py 里"没配 DEEPSEEK_API_KEY 就跳过真实调用"是同一个思路。
_SAKURA_BASE_URL = os.environ.get("SAKURA_BASE_URL", "http://192.168.32.62:11434/v1")
_SAKURA_MODEL = os.environ.get("SAKURA_MODEL", "sakura-galtransl")


def _sakura_reachable() -> bool:
    parsed = urlparse(_SAKURA_BASE_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=1.5):
            return True
    except OSError:
        return False


def _make_job(source_text: str, context: str = "") -> Job:
    protected_text, mapping = protect(source_text)
    return Job(source_text, [], protected_text, mapping, context)


def test_build_single_prompt_without_context_has_empty_history():
    prompt = _build_single_prompt("こんにちは", "")
    assert "[History]" not in prompt
    assert "[Input]" not in prompt
    assert prompt.startswith("参考以下术语表")
    assert prompt.endswith("こんにちは")


def test_build_single_prompt_with_context_fills_history_slot():
    prompt = _build_single_prompt("こんにちは", "村の入り口での会話")
    assert prompt.startswith("历史剧情：村の入り口での会話\n")
    assert prompt.endswith("こんにちは")


def test_build_single_prompt_escapes_real_newlines():
    prompt = _build_single_prompt("第一行\n第二行", "")
    assert "第一行\\n第二行" in prompt
    assert "第一行\n第二行" not in prompt


def test_build_batch_prompt_joins_lines_and_uses_shared_job_context():
    jobs = [_make_job("こんにちは", context="村の会話"), _make_job("さようなら", context="村の会話")]
    prompt = _build_batch_prompt(jobs)
    assert "历史剧情：村の会話" in prompt
    assert "こんにちは\nさようなら" in prompt


def test_build_batch_prompt_omits_history_when_batch_mixes_different_contexts():
    """一个批次可能打包了来自不同事件的条目——[History] 是整批共享的槽位，没法
    按条目区分，批次内 context 不一致时不应该把某一条的背景错误地安到其它条目
    头上（宁可不给背景，也不要给错的）。"""
    jobs = [_make_job("こんにちは", context="事件A的剧情"), _make_job("さようなら", context="事件B的剧情")]
    prompt = _build_batch_prompt(jobs)
    # 模板本身固定含有"结合历史剧情和上下文"这句说明文字，只断言 [History] 槽位
    # 真正被填充时才会出现的"历史剧情：xxx"没有被注入
    assert "历史剧情：" not in prompt
    assert "事件A的剧情" not in prompt
    assert "事件B的剧情" not in prompt


def test_parse_batch_response_line_count_mismatch_returns_none():
    assert _parse_batch_response("只有一行", 2) is None


def test_parse_batch_response_well_formed_unescapes_newlines():
    result = _parse_batch_response("你好\n第一行\\n第二行", 2)
    assert result == {1: "你好", 2: "第一行\n第二行"}


class _SakuraStub:
    """模拟按官方协议老实回复的 Sakura：输出行数、顺序与输入一一对应，不带任何标签。"""

    def __init__(self):
        self.call_count = 0
        self.last_user_prompt = ""

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        assert system_prompt == SAKURA_SYSTEM_PROMPT
        self.call_count += 1
        self.last_user_prompt = user_prompt
        input_marker = "简体中文：\n"
        idx = user_prompt.index(input_marker) + len(input_marker)
        input_str = user_prompt[idx:]
        lines = input_str.split("\n")
        return "\n".join(f"译文:{line}" for line in lines)


def _make_unit(uid: str, source_text: str, context: str = "") -> TextUnit:
    return TextUnit(
        id=uid,
        engine="mz",
        file_path="data/Map001.json",
        locator=f"events/1/pages/0/list/{uid}/parameters/0",
        context=context,
        source_text=source_text,
        status="pending",
    )


@pytest.mark.anyio
async def test_translate_units_with_sakura_strategy_batches_by_line(tmp_path: Path):
    stub = _SakuraStub()
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}号") for i in range(3)]
        store.upsert_units(units)

        failures = await translate_units(
            stub, store, units, concurrency=4, batch_size=25,
            prompt_strategy=SAKURA_PROMPT_STRATEGY,
        )

        assert failures == []
        assert stub.call_count == 1  # 3 条一次性打包成 1 次请求
        for i in range(3):
            assert store.get_unit(str(i)).translated_text == f"译文:文本{i}号"


@pytest.mark.anyio
async def test_translate_units_with_sakura_strategy_falls_back_when_line_count_mismatches(
    tmp_path: Path,
):
    class _DropsALineOnceStub:
        def __init__(self):
            self.call_count = 0

        async def chat(self, system_prompt: str, user_prompt: str) -> str:
            self.call_count += 1
            input_marker = "简体中文：\n"
            idx = user_prompt.index(input_marker) + len(input_marker)
            input_str = user_prompt[idx:]
            lines = input_str.split("\n")
            if self.call_count == 1:
                lines = lines[:-1]  # 第一次少输出最后一行，逼出按行数校验失败
            return "\n".join(f"译文:{line}" for line in lines)

    stub = _DropsALineOnceStub()
    with Store(tmp_path / "units.db") as store:
        units = [_make_unit(str(i), f"文本{i}号") for i in range(3)]
        store.upsert_units(units)

        failures = await translate_units(
            stub, store, units, concurrency=4, batch_size=25,
            prompt_strategy=SAKURA_PROMPT_STRATEGY,
        )

        assert failures == []
        for i in range(3):
            assert store.get_unit(str(i)).translated_text == f"译文:文本{i}号"


@pytest.mark.anyio
async def test_translate_units_against_real_sakura_server(tmp_path: Path):
    """对局域网里部署的真实 Sakura-GalTransl 模型跑一次端到端验证：控制码占位符
    能不能在这套跟我们自己协议完全不同的模板下也原样往返，以及批量按行对齐是否
    真的可靠。"""
    if not _sakura_reachable():
        pytest.skip(f"本地连不上 Sakura 测试服务器 {_SAKURA_BASE_URL}，跳过真实调用测试")

    config = LLMConfig(
        api_key="sk-sakura", base_url=_SAKURA_BASE_URL, model=_SAKURA_MODEL, timeout=120.0
    )
    with Store(tmp_path / "units.db") as store:
        units = [
            _make_unit(
                "1", "\\C[1]こんにちは、旅人よ。\\C[0]", context="村の入り口での会話"
            ),
            _make_unit("2", "この村へようこそ。", context="村の入り口での会話"),
            _make_unit("3", "宝箱を見つけた！"),
        ]
        store.upsert_units(units)

        async with LLMClient(config) as client:
            failures = await translate_units(
                client, store, units, concurrency=2, batch_size=25,
                prompt_strategy=SAKURA_PROMPT_STRATEGY,
            )

        assert failures == []
        result = store.get_unit("1")
        assert result.status == "translated"
        assert "\\C[1]" in result.translated_text
        assert "\\C[0]" in result.translated_text
        assert "⟦CC" not in result.translated_text
        for uid in ("2", "3"):
            assert store.get_unit(uid).status == "translated"
            assert store.get_unit(uid).translated_text
