from __future__ import annotations

import asyncio
import re
from typing import Callable, NamedTuple

from rpg_translator.codec.control_codes import protect, restore
from rpg_translator.core.ir import TextUnit, compute_source_hash
from rpg_translator.core.store import Store
from rpg_translator.translate.llm_client import LLMClient

_TRANSLATE_SYSTEM_PROMPT_TEMPLATE = (
    "你是一个专业的游戏本地化翻译，将日文 RPG 游戏文本翻译成简体中文。规则：\n"
    "1. `⟦CCn⟧` 形式的占位符是不可翻译、不可移动位置、不可增删的控制码标记，必须原样保留，"
    "两侧不要增删空格。\n"
    "2. 保持角色对话的语气和人称，不要过度意译。\n"
    "3. 术语表里出现的词必须使用给定的固定译名。\n"
    "4. 只输出译文本身，不要输出解释、引号或任何多余内容。"
    "{glossary_section}"
)

# 一次请求最多打包多少条不同原文一起翻译。几万行文本量级下，一行一请求在时间和 token 成本
# 上都不现实（每次请求都要重复付一遍 system prompt 的 token），打包成批次是主要的省钱手段，
# 配合 DeepSeek 的 context caching（system prompt 不变，能吃到缓存折扣）。
DEFAULT_BATCH_SIZE = 25

_BATCH_INSTRUCTION = (
    "请把下面编号的文本逐条翻译成简体中文。每条译文必须以 [编号] 开头另起一行，"
    "编号要和输入的编号一一对应，不要合并、不要跳号、不要输出编号以外的任何解释性文字。"
)
_ITEM_MARKER_RE = re.compile(r"^\[(\d+)\]\s*", re.MULTILINE)


def _build_system_prompt(glossary: dict[str, str]) -> str:
    if not glossary:
        return _TRANSLATE_SYSTEM_PROMPT_TEMPLATE.format(glossary_section="")
    lines = "\n".join(f"- {term} -> {translation}" for term, translation in glossary.items())
    glossary_section = f"\n\n术语表（必须使用以下固定译名）：\n{lines}"
    return _TRANSLATE_SYSTEM_PROMPT_TEMPLATE.format(glossary_section=glossary_section)


class _StopRequested(Exception):
    """内部信号：翻译过程中用户点了停止，这次调用被主动打断——不是真正的 API 报错，
    调用方要把它和内容审核拒绝/网络失败之类的失败区分开，不计入失败列表。"""


# 停止检查的轮询间隔：点了"停止"之后，最多这么久就会真正打断一个正在等待响应的
# 请求（提前 cancel 掉底层 HTTP 调用），而不是傻等它自然跑完——批量打包 + 高并发下，
# 一次请求可能覆盖几十条文本、耗时数秒到数十秒，不这么做的话"停止"要等很久才生效，
# 期间还在继续消耗 token。
_CANCEL_POLL_INTERVAL = 0.2

# 一轮 pending 批次跑完后，仍失败的条目（比如当时所有 provider 恰好都在限流/抖动）
# 原地自动重试的轮数和轮间等待——LLMClient.chat 内部已经对单次调用做过重试+故障
# 转移，这里的失败是用尽底层手段后的结果，多等几秒再整体重跑一次，用来应对"过一会
# 就恢复"的瞬时性问题，不是无意义的立即重试。
_AUTO_RETRY_ROUNDS = 2
_AUTO_RETRY_WAIT_SECONDS = 5.0


async def _interruptible_sleep(seconds: float, cancelled: Callable[[], bool]) -> None:
    """可被 cancel_check 打断的等待：不用户点了停止还要傻等满这几秒才响应。"""
    remaining = seconds
    while remaining > 0 and not cancelled():
        step = min(_CANCEL_POLL_INTERVAL, remaining)
        await asyncio.sleep(step)
        remaining -= step


async def _chat_cancellable(
    client: LLMClient, system_prompt: str, user_prompt: str, cancelled: Callable[[], bool]
) -> str:
    task = asyncio.ensure_future(client.chat(system_prompt, user_prompt))
    while not task.done():
        if cancelled():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise _StopRequested()
        await asyncio.wait({task}, timeout=_CANCEL_POLL_INTERVAL)
    return task.result()


class _Job(NamedTuple):
    source_text: str
    group: list[TextUnit]
    protected_text: str
    mapping: dict[str, str]
    context: str


def _build_single_user_prompt(protected_text: str, context: str) -> str:
    if context:
        return f"上下文：\n{context}\n\n待翻译文本：\n{protected_text}"
    return f"待翻译文本：\n{protected_text}"


def _build_batch_user_prompt(items: list[_Job]) -> str:
    parts = [_BATCH_INSTRUCTION]
    for i, job in enumerate(items, start=1):
        if job.context:
            parts.append(f"[{i}] 上下文：{job.context}\n待翻译：{job.protected_text}")
        else:
            parts.append(f"[{i}] 待翻译：{job.protected_text}")
    return "\n\n".join(parts)


def _parse_batch_response(response: str, expected_count: int) -> dict[int, str] | None:
    matches = list(_ITEM_MARKER_RE.finditer(response))
    if len(matches) != expected_count:
        return None

    result: dict[int, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(response)
        try:
            index = int(m.group(1))
        except ValueError:
            return None
        result[index] = response[start:end].strip()

    if set(result.keys()) != set(range(1, expected_count + 1)):
        return None
    return result


async def translate_units(
    client: LLMClient,
    store: Store,
    units: list[TextUnit],
    glossary: dict[str, str],
    concurrency: int = 4,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cancel_check: Callable[[], bool] | None = None,
    auto_retry_rounds: int = _AUTO_RETRY_ROUNDS,
    retry_wait_seconds: float = _AUTO_RETRY_WAIT_SECONDS,
) -> list[tuple[str, str]]:
    """按 source_text 去重分组，相同原文只调用一次 LLM；多个不同分组打包进同一次请求
    （见 batch_size），减少大文本量下的请求数和重复的 system prompt token 开销。

    on_progress(completed, total) 在每个去重分组处理完（无论成功还是失败）后调用一次，
    供 GUI 显示"翻译批次 X/Y" 进度用（见 spec 第 10 节），不传则跳过。

    cancel_check() 在每个批次真正发请求前检查一次：返回 True 就跳过这个批次，不发起
    新的 API 调用（对应用户点了"停止"或者软件被要求中断）。已经在缓存命中路径写完的
    结果、以及调用这个函数之前就已经在途的批次不受影响——已经花出去的 token 不浪费，
    只是不再新增。

    单条 LLM 调用即使重试完所有 provider 依然失败（比如某条文本被内容审核拒绝、返回
    不可重试的 4xx），也只跳过那一条——保持 status="pending" 供下次重跑续译，不会像
    asyncio.gather 默认行为那样一条报错就取消掉同批甚至其它并发批次里已经在跑的翻译。

    第一轮跑完所有 pending 分组后，仍失败的条目会原地自动重试最多 _AUTO_RETRY_ROUNDS
    轮、轮间隔 _AUTO_RETRY_WAIT_SECONDS 秒（应对"当时所有 provider 恰好都在限流/抖动，
    隔几秒会恢复"的场景）；重试轮不会重复计入 on_progress 的 completed 计数。点了停止
    会在轮次之间的等待、以及每轮发请求前被检查到，不会傻等完剩余轮次。

    返回值是最终仍失败条目的 (source_text, 错误信息) 列表，供调用方汇报"N 条被跳过"。
    """
    system_prompt = _build_system_prompt(glossary)
    semaphore = asyncio.Semaphore(concurrency)

    groups: dict[str, list[TextUnit]] = {}
    for unit in units:
        if unit.status != "pending":
            continue
        groups.setdefault(unit.source_text, []).append(unit)

    jobs: list[_Job] = []
    cache_hits: list[tuple[list[TextUnit], str]] = []
    for source_text, group in groups.items():
        cached = store.get_memory(compute_source_hash(source_text))
        if cached is not None:
            cache_hits.append((group, cached))
        else:
            protected_text, mapping = protect(source_text)
            jobs.append(_Job(source_text, group, protected_text, mapping, group[0].context))

    total = len(jobs) + len(cache_hits)
    completed = 0
    failures: list[tuple[str, str]] = []

    def _write_result(
        group: list[TextUnit], translated_text: str, count_progress: bool = True
    ) -> None:
        nonlocal completed
        for unit in group:
            store.update_translation(unit.id, translated_text, status="translated")
        if count_progress:
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)

    def _record_failure(job: _Job, error: BaseException, count_progress: bool = True) -> None:
        nonlocal completed
        failures.append((job.source_text, str(error)))
        if count_progress:
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)

    for group, cached in cache_hits:
        _write_result(group, cached)

    def _cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    async def _translate_single_job(job: _Job, count_progress: bool = True) -> None:
        user_prompt = _build_single_user_prompt(job.protected_text, job.context)
        async with semaphore:
            # 取消检查放在拿到并发名额之后、真正发请求之前：还在排队等名额的批次，
            # 轮到它的时候如果已经被取消就直接放弃，不发这次请求；但已经拿到名额、
            # 正在等待响应的调用不会被这个检查打断，等它自然跑完并落盘。
            if _cancelled():
                return
            try:
                raw = await _chat_cancellable(client, system_prompt, user_prompt, _cancelled)
            except _StopRequested:
                return  # 被停止打断，保留 pending，不计入失败，下次重跑续译
            except Exception as e:  # noqa: BLE001 - 单条失败只跳过，不拖累其它条目
                _record_failure(job, e, count_progress)
                return
        translated_text = restore(raw.strip(), job.mapping)
        store.set_memory(compute_source_hash(job.source_text), job.source_text, translated_text)
        _write_result(job.group, translated_text, count_progress)

    async def _translate_batch(batch: list[_Job], count_progress: bool = True) -> None:
        if len(batch) == 1:
            await _translate_single_job(batch[0], count_progress)
            return

        user_prompt = _build_batch_user_prompt(batch)
        async with semaphore:
            if _cancelled():
                return
            try:
                raw = await _chat_cancellable(client, system_prompt, user_prompt, _cancelled)
            except _StopRequested:
                return  # 被停止打断，整批保留 pending，不计入失败，下次重跑续译
            except Exception:
                # 整批请求失败（比如批里某一条被内容审核拒绝，导致打包请求整体报错），
                # 退化成逐条调用——批里没问题的条目仍然能各自成功，只有真正有问题的
                # 那一条会在 _translate_single_job 里被单独记录为失败并跳过。
                await asyncio.gather(
                    *(_translate_single_job(job, count_progress) for job in batch)
                )
                return

        parsed = _parse_batch_response(raw, len(batch))
        if parsed is None:
            # 模型没按格式回，退化成逐条调用，保证正确性（牺牲这一批的省 token 收益）
            await asyncio.gather(*(_translate_single_job(job, count_progress) for job in batch))
            return

        for i, job in enumerate(batch, start=1):
            translated_text = restore(parsed[i], job.mapping)
            store.set_memory(compute_source_hash(job.source_text), job.source_text, translated_text)
            _write_result(job.group, translated_text, count_progress)

    batches = [jobs[i : i + batch_size] for i in range(0, len(jobs), batch_size)]
    await asyncio.gather(*(_translate_batch(batch) for batch in batches))

    jobs_by_source = {job.source_text: job for job in jobs}
    for _ in range(auto_retry_rounds):
        if not failures or _cancelled():
            break
        await _interruptible_sleep(retry_wait_seconds, _cancelled)
        if _cancelled():
            break
        retry_jobs = [jobs_by_source[source_text] for source_text, _error in failures]
        failures.clear()
        retry_batches = [
            retry_jobs[i : i + batch_size] for i in range(0, len(retry_jobs), batch_size)
        ]
        await asyncio.gather(
            *(_translate_batch(batch, count_progress=False) for batch in retry_batches)
        )

    return failures
