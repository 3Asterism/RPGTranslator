from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Callable, NamedTuple

from rpg_translator.codec.control_codes import extract_codes, protect, restore
from rpg_translator.core.ir import TextUnit, compute_source_hash
from rpg_translator.core.store import Store
from rpg_translator.translate.llm_client import LLMClient

_TRANSLATE_SYSTEM_PROMPT = (
    "你是专业的日译中游戏本地化翻译。规则：\n"
    "1. ⟦CCn⟧ 占位符代表控制码，必须原样保留、不可翻译/移动/增删，两侧不加空格。\n"
    "2. 保留角色语气和人称，不过度意译。\n"
    "3. 只输出译文，不要解释、引号或多余内容。\n"
    "4. 如果提供了「上下文」，那只是帮你理解语境的背景资料，绝对不要翻译或输出"
    "上下文的内容——只翻译、只输出「待翻译」标记的那一句。\n"
    "5. 输出里不能出现「上下文」「待翻译」这类标签字样本身，也不能把背景对话"
    "复述或翻译进来——译文应该只比原文这一句本身长，不会因为夹带背景内容而"
    "明显变长。"
)

# 一次请求最多打包多少条不同原文一起翻译。几万行文本量级下，一行一请求在时间和 token 成本
# 上都不现实（每次请求都要重复付一遍 system prompt 的 token），打包成批次是主要的省钱手段，
# 配合 DeepSeek 的 context caching（system prompt 不变，能吃到缓存折扣）。
#
# 这个值也是控制请求数（进而是撞上 provider RPM 限流概率）的主要杠杆，但不是越大越好：
# 批次里任何一条译文格式跑偏（多/少一行、漏编号、夹带解释文字）都会导致整批解析失败、
# 退化成二分重试——批次越大，命中"至少一条跑偏"的概率越高（复合概率，不是线性），
# 实测无论本地小模型还是在线 DeepSeek/百炼，默认值 50 都会频繁触发"批次回复解析失败"，
# 手动调到 20 之后明显改善（见批量翻译时的实测反馈），故改默认值为 20。
#
# 批次内部不是随便凑数：同一个事件页面（TextUnit.context_group）的台词会尽量分进
# 同一批（见 _chunk_jobs_by_group），是这个数字的软上限，不是硬性每批必须凑满这么多。
DEFAULT_BATCH_SIZE = 20

_BATCH_INSTRUCTION = (
    "逐条翻译下面编号的文本。每条译文以 [编号] 开头另起一行，编号需与输入一一对应，"
    "不合并、不跳号、不输出编号外的文字。每条里的「上下文」只是背景参考，不要翻译"
    "或输出上下文本身，只翻译「待翻译」那一句，不要在该条译文里出现「上下文」"
    "「待翻译」这类标签字样或复述背景对话。如果连续多条编号本身就是同一段场景里的"
    "连续台词，请让人名、称呼、术语在这些条目之间前后保持一致。"
)
_ITEM_MARKER_RE = re.compile(r"^\[(\d+)\]\s*", re.MULTILINE)


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
    client: LLMClient,
    system_prompt: str,
    user_prompt: str,
    cancelled: Callable[[], bool],
    extra_body: dict | None = None,
) -> str:
    task = asyncio.ensure_future(client.chat(system_prompt, user_prompt, extra_body))
    while not task.done():
        if cancelled():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise _StopRequested()
        await asyncio.wait({task}, timeout=_CANCEL_POLL_INTERVAL)
    return task.result()


class Job(NamedTuple):
    source_text: str
    group: list[TextUnit]
    protected_text: str
    mapping: dict[str, str]
    context: str
    context_group: str
    # "\n<角色名>正文" 这种说话人标记写法（见 _split_speaker_tag）被拆成名字/正文
    # 分开翻译时，这里存翻好的 "\n<译名>" 前缀，写回结果时直接拼在模型翻译的正文
    # 前面——非空时说明这个 job 的 source_text/protected_text 已经是拆出来的正文，
    # 不是 TextUnit.source_text 原文整句。
    result_prefix: str = ""


_SPEAKER_TAG_SOURCE_RE = re.compile(r"^\\n<([^>\n]*)>(.*)\Z", re.DOTALL)


def _split_speaker_tag(source_text: str) -> tuple[str, str] | None:
    """"\\n<角色名>正文" 这种说话人标记写法是真实 RPG Maker MV 工程里实测到的用法：
    \\n 是普通换行控制码，尖括号是插件/作者自己的说话人标记约定，不是标准控制码
    语法。protect() 只能把尖括号本身当占位符保护住、名字仍暴露给模型翻译（见
    codec/control_codes.py），但这仍然要求模型老实保留两个占位符——实测虽然加了
    校验+自动重试兜底，还是不如干脆不让模型看到尖括号可靠。

    这里在更上一层直接把 "角色名" 和 "正文" 拆成两个独立的翻译任务分别处理（见
    translate_units），模型全程看不到 "\\n<" ">" 这几个字符，写回结果时由代码自己
    拼成 "\\n<译名>译正文"，从根上消灭"模型该不该保留这段尖括号"的判断失误。

    没匹配上（不是这个写法，或者尖括号里是空的）返回 None，走原来的整句
    protect()/翻译流程。"""
    m = _SPEAKER_TAG_SOURCE_RE.match(source_text)
    if m is None:
        return None
    name, rest = m.group(1), m.group(2)
    if not name or not rest:
        # 名字为空、或者标签后面没有正文（比如整句就是 "\n<角色名>"，没有实际
        # 台词）——没什么好拆的，交给原来的整句 protect()/翻译流程处理。
        return None
    return name, rest


def _build_single_user_prompt(protected_text: str, context: str) -> str:
    if context:
        return (
            f"上下文（仅供理解语境，不要翻译，不要输出）：\n{context}\n\n"
            f"待翻译文本（只翻译并只输出这一句）：\n{protected_text}"
        )
    return f"待翻译文本：\n{protected_text}"


def _build_batch_user_prompt(items: list[Job]) -> str:
    parts = [_BATCH_INSTRUCTION]
    for i, job in enumerate(items, start=1):
        if job.context:
            parts.append(
                f"[{i}] 上下文（不要翻译）：{job.context}\n待翻译（只翻译这句）：{job.protected_text}"
            )
        else:
            parts.append(f"[{i}] 待翻译：{job.protected_text}")
    return "\n\n".join(parts)


# A/B 测试发现的真实事故：模型有时不老实遵守"只翻译待翻译那一句"的指令，把整个
# 「上下文」也一起翻译/复述进回复里——一句几个字的原文，存进库里的"译文"变成一整段
# 不相关对话，真正的译文被埋在最后（例如回复里出现"...一大段对话...待翻译：真正答案"）。
# 旧代码把模型回复原样落盘，没做任何校验，扫库发现至少 6.6% 的已翻译内容因此变成
# 驴唇不对马嘴的文字。用两个信号识别这种跑题回复，命中就判失败交给上层重试：
_CONTEXT_LEAK_MARKERS = ("待翻译文本", "待翻译：", "待翻译(", "待翻译（", "上下文（", "上下文：")
# 正常日译中不会把一句话翻出 3 倍以上的字数；但短原文（几个字）自然膨胀比例也会很
# 高，所以额外要求绝对长度也达到一定量级，避免把正常的短句翻译误判为泄漏。
_LEAK_LENGTH_RATIO = 3
_LEAK_LENGTH_MIN_CHARS = 40


def _looks_like_leaked_context(protected_text: str, translated: str) -> bool:
    if any(marker in translated for marker in _CONTEXT_LEAK_MARKERS):
        return True
    return (
        len(translated) >= _LEAK_LENGTH_MIN_CHARS
        and len(translated) > len(protected_text) * _LEAK_LENGTH_RATIO
    )


def _has_all_placeholders(raw: str, mapping: dict[str, str]) -> bool:
    """protect() 把控制码换成了模型看不懂内容、只需要原样保留位置的 ⟦CCn⟧ 占位符——
    模型不该、也没法把控制码本身翻错，唯一还可能出错的是把某个占位符整个漏掉（小
    模型实测出现过，见 llm 选型记录）。restore() 只会替换文本里存在的占位符，
    漏掉的会悄悄从译文里消失且不报错，所以这里在 restore 之前显式校验一遍完整性。"""
    return all(token in raw for token in mapping)


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


@dataclass(frozen=True)
class PromptStrategy:
    """把"怎么问模型、怎么解析回复"从 translate_units 的去重/缓存/并发/重试骨架里
    抽出来——不同 provider 可能有完全不同的 prompt 模板和批量协议（比如量身微调过
    的本地小模型，不认识我们这套 [编号] 批量格式，见 sakura_prompt.py），只要实现
    这三个函数就能复用同一套翻译流程，不用另起一套并行实现。"""

    system_prompt: str
    build_single_prompt: Callable[[str, str], str]
    build_batch_prompt: Callable[[list[Job]], str]
    # (response, expected_count) -> {1-based 序号: 该条译文}，None 表示解析失败
    parse_batch_response: Callable[[str, int], dict[int, str] | None]
    # protect() 把控制码换成 ⟦CCn⟧ 占位符是为了防住"模型把控制码本身翻译/挪动/漏译"
    # ——但这个假设是"模型能可靠地原样保留一个它没见过的、生僻的 unicode 占位符"。
    # 实测在 Sakura-GalTransl 这类照着原生控制符（\C[1] 这种反斜杠转义）训练过的
    # 本地小模型上恰恰相反：⟦CCn⟧ 占位符经常被抄错/加乱码（约四到六成概率），原始
    # 反斜杠控制码反而 8/8 次原样保留（见适配测试记录）——对这类 provider 应该关掉
    # 占位符包装，让控制码原样透传。关闭后 Job.mapping 恒为空，下游的占位符校验
    # （_has_all_placeholders 对空 mapping 永远返回 True）和 restore()（空 mapping
    # 下是恒等操作）自动退化成"不做任何事"，不需要额外分支。
    wrap_control_codes: bool = True
    # 随请求体一起发给 provider 的采样参数覆盖（temperature/top_p 等，OpenAI 兼容
    # 字段名）。不同 provider 的合理默认值差别很大：DeepSeek 之类通用云端模型没有
    # 已知需要偏离默认值的证据，留空不覆盖；专门微调过的本地小模型（见
    # sakura_prompt.py）有官方推荐的低温度设置，不覆盖的话吃的是模型部署时
    # Modelfile 里的默认值——那个值不一定等于官方推荐（实测部署环境里是
    # temperature=0.3/top_p=0.8，官方推荐 0.1/0.3），温度越高越容易触发批量格式
    # 跑偏/夹带上下文这类需要重试的问题。
    extra_body: dict = field(default_factory=dict)


DEFAULT_PROMPT_STRATEGY = PromptStrategy(
    system_prompt=_TRANSLATE_SYSTEM_PROMPT,
    build_single_prompt=_build_single_user_prompt,
    build_batch_prompt=_build_batch_user_prompt,
    parse_batch_response=_parse_batch_response,
)


def _chunk_jobs_by_group(jobs: list[Job], batch_size: int) -> list[list[Job]]:
    """把 job 按 context_group 切成批次：同一个 context_group（比如同一个事件页面）的
    job 尽量分进同一批里，让它们在同一次请求里天然共享上下文、保持人名/术语前后一致
    （段落进段落出——实测对照见 CLAUDE.md 的调研记录：拆成多次独立请求不但更费 token
    ——每次都要重复付一遍固定模板的开销——译名还会在请求之间漂移）。不同 context_group
    的边界处强制切批，不让不相关场景的台词混进同一次请求。空 context_group（数据库
    字段这类没有自然顺序、不需要共享上下文的条目）视为统一的一个分组，退化成原来纯按
    batch_size 切块的行为。每批仍然不超过 batch_size 条，超长的单个分组会被拆成多批。"""
    batches: list[list[Job]] = []
    current: list[Job] = []
    current_group: str | None = None
    for job in jobs:
        if current and (len(current) >= batch_size or job.context_group != current_group):
            batches.append(current)
            current = []
        current.append(job)
        current_group = job.context_group
    if current:
        batches.append(current)
    return batches


async def translate_units(
    client: LLMClient,
    store: Store,
    units: list[TextUnit],
    concurrency: int = 4,
    on_progress: Callable[[int, int], None] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cancel_check: Callable[[], bool] | None = None,
    auto_retry_rounds: int = _AUTO_RETRY_ROUNDS,
    retry_wait_seconds: float = _AUTO_RETRY_WAIT_SECONDS,
    prompt_strategy: PromptStrategy = DEFAULT_PROMPT_STRATEGY,
    on_log: Callable[[str], None] | None = None,
) -> list[tuple[str, str]]:
    """按 source_text 去重分组，相同原文只调用一次 LLM；多个不同分组打包进同一次请求
    （见 batch_size），减少大文本量下的请求数和重复的 system prompt token 开销。同一个
    事件页面（TextUnit.context_group）的分组会尽量分进同一批（见 _chunk_jobs_by_group），
    让这段台词在同一次请求里当成一整段翻译，人名/术语靠模型自己在这次请求内部保持
    一致，不需要再给每条各自拼一份背景上下文。

    on_progress(completed, total) 在每个去重分组处理完（无论成功还是失败）后调用一次，
    供 GUI 显示"翻译批次 X/Y" 进度用（见 spec 第 10 节），不传则跳过。

    on_log(message) 在单条/整批请求失败、批次退化拆分、自动重试轮开始时各调用一次，
    供 GUI 在日志框里实时展示这些中间状态（不然进度条卡住时用户分不清是真卡住了还是
    正在退避重试），不传则跳过。

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
    system_prompt = prompt_strategy.system_prompt
    semaphore = asyncio.Semaphore(concurrency)

    # "\n<角色名>正文" 写法先拆出名字单独翻译（见 _split_speaker_tag），结果落进
    # 翻译记忆库，供下面构造正文 job 时直接查表拼前缀——只在默认占位符包装策略下
    # 做这个拆分，Sakura 本地模型走的是完全不同的批量格式，不覆盖这个机制。
    speaker_split: dict[str, tuple[str, str]] = {}  # unit.id -> (角色名, 正文)
    if prompt_strategy.wrap_control_codes:
        for unit in units:
            if unit.status != "pending":
                continue
            split = _split_speaker_tag(unit.source_text)
            if split is not None:
                speaker_split[unit.id] = split

    name_translations: dict[str, str] = {}
    if speaker_split:
        distinct_names = list(dict.fromkeys(name for name, _ in speaker_split.values()))
        name_units = [
            TextUnit(
                id=f"__speaker_name__{i}",
                engine=units[0].engine,
                file_path="",
                locator="",
                context="",
                source_text=name,
                status="pending",
            )
            for i, name in enumerate(distinct_names)
        ]
        # 复用同一套翻译流程翻名字：去重、批量打包、失败重试、取消响应全部继承，
        # 不用另写一套。name_units 是内存里现造的临时 TextUnit，不会被 upsert 进
        # 数据库——update_translation 对不存在的 id 只是一次无操作的 UPDATE（0 行
        # 受影响，不报错），真正需要的是这次调用顺带把翻译结果写进 store 的翻译
        # 记忆库（下面按原文查表取）。
        await translate_units(
            client, store, name_units, concurrency,
            batch_size=batch_size, cancel_check=cancel_check,
            auto_retry_rounds=auto_retry_rounds, retry_wait_seconds=retry_wait_seconds,
            prompt_strategy=prompt_strategy, on_log=on_log,
        )
        for name in distinct_names:
            cached = store.get_memory(compute_source_hash(name))
            # 查不到（比如取消了，或者这个名字翻译重试用尽还是失败）就退化成用
            # 原名——不能让整条线因为名字这一小部分翻不出来就跟着失败/卡住。
            name_translations[name] = cached if cached is not None else name

    groups: dict[tuple[str, str], list[TextUnit]] = {}  # (待翻译正文, 结果前缀) -> units
    for unit in units:
        if unit.status != "pending":
            continue
        split = speaker_split.get(unit.id)
        if split is not None:
            name, rest = split
            key = (rest, f"\\n<{name_translations[name]}>")
        else:
            key = (unit.source_text, "")
        groups.setdefault(key, []).append(unit)

    jobs: list[Job] = []
    cache_hits: list[tuple[list[TextUnit], str]] = []
    for (source_text, result_prefix), group in groups.items():
        cached = store.get_memory(compute_source_hash(source_text))
        if cached is not None:
            cache_hits.append((group, result_prefix + cached))
        else:
            if prompt_strategy.wrap_control_codes:
                protected_text, mapping = protect(source_text)
            else:
                # 不包装占位符，但控制码本身原样交给模型——仍然要能校验模型是不是
                # 老实保留了这些码，否则校验形同虚设（见 wrap_control_codes 的说明）。
                # 用恒等映射复用现成的 _has_all_placeholders/restore：mapping 里
                # token 和 code 相同，"token 是否在译文里" 就是 "这个控制码是否被
                # 保留"，restore() 对恒等映射来说是空操作，不会误改译文。
                protected_text = source_text
                mapping = {code: code for code in extract_codes(source_text)}
            jobs.append(
                Job(
                    source_text,
                    group,
                    protected_text,
                    mapping,
                    group[0].context,
                    group[0].context_group,
                    result_prefix,
                )
            )

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

    def _record_failure(job: Job, error: BaseException, count_progress: bool = True) -> None:
        nonlocal completed
        failures.append((job.source_text, str(error)))
        if on_log is not None:
            preview = job.source_text if len(job.source_text) <= 20 else job.source_text[:20] + "…"
            on_log(f"翻译失败，已跳过：{preview!r} - {error}")
        if count_progress:
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)

    for group, cached in cache_hits:
        _write_result(group, cached)

    def _cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    async def _translate_single_job(job: Job, count_progress: bool = True) -> None:
        user_prompt = prompt_strategy.build_single_prompt(job.protected_text, job.context)
        async with semaphore:
            # 取消检查放在拿到并发名额之后、真正发请求之前：还在排队等名额的批次，
            # 轮到它的时候如果已经被取消就直接放弃，不发这次请求；但已经拿到名额、
            # 正在等待响应的调用不会被这个检查打断，等它自然跑完并落盘。
            if _cancelled():
                return
            try:
                raw = await _chat_cancellable(
                    client, system_prompt, user_prompt, _cancelled, prompt_strategy.extra_body
                )
            except _StopRequested:
                return  # 被停止打断，保留 pending，不计入失败，下次重跑续译
            except Exception as e:  # noqa: BLE001 - 单条失败只跳过，不拖累其它条目
                _record_failure(job, e, count_progress)
                return
        stripped = raw.strip()
        if _looks_like_leaked_context(job.protected_text, stripped):
            # 模型把「上下文」也翻译/复述进了回复——按失败处理而不是把这一大段不
            # 相关文本原样存成"译文"，让自动重试轮有机会重新问一次模型。
            _record_failure(job, RuntimeError("译文疑似夹带上下文内容，已跳过"), count_progress)
            return
        if not _has_all_placeholders(stripped, job.mapping):
            # 模型漏掉了控制码占位符——按失败处理而不是原样写入残缺译文，让自动
            # 重试轮有机会重新问一次模型（见 translate_units 的 auto_retry_rounds）。
            _record_failure(job, RuntimeError("译文丢失控制码占位符，已跳过"), count_progress)
            return
        translated_text = restore(stripped, job.mapping)
        store.set_memory(compute_source_hash(job.source_text), job.source_text, translated_text)
        _write_result(job.group, job.result_prefix + translated_text, count_progress)

    async def _bisect_batch(batch: list[Job], count_progress: bool) -> None:
        # 整批请求失败/解析失败时的退化路径：二分成两半各自重新走 _translate_batch，
        # 而不是直接拆成 len(batch) 次单条调用——多数情况下问题只集中在其中一部分
        # （批里某一条被内容审核拒绝导致打包请求整体报错；或者模型偶尔多吐/漏吐了
        # 一行导致行数对不齐），另一半下一轮还能整批过，不用全部陪葬成单条请求。
        # 递归二分到剩一条时 _translate_batch 会自然退化成 _translate_single_job，
        # 真正有问题的条目照样能被单独定位、单独重试。
        mid = len(batch) // 2
        await asyncio.gather(
            _translate_batch(batch[:mid], count_progress),
            _translate_batch(batch[mid:], count_progress),
        )

    async def _translate_batch(batch: list[Job], count_progress: bool = True) -> None:
        if len(batch) == 1:
            await _translate_single_job(batch[0], count_progress)
            return

        user_prompt = prompt_strategy.build_batch_prompt(batch)
        request_failed = False
        raw: str | None = None
        async with semaphore:
            if _cancelled():
                return
            try:
                raw = await _chat_cancellable(
                    client, system_prompt, user_prompt, _cancelled, prompt_strategy.extra_body
                )
            except _StopRequested:
                return  # 被停止打断，整批保留 pending，不计入失败，下次重跑续译
            except Exception as e:
                if on_log is not None:
                    on_log(f"批次请求失败（{len(batch)} 条），拆分重试：{e}")
                request_failed = True

        if request_failed:
            # 二分重试要重新抢并发名额，绝不能在还攥着这一个名额的时候直接递归调用——
            # 并发数一低、又好巧不巧撞上好几个批次同时失败，子任务要抢的名额会永远
            # 轮不到（全被还没释放的上级占着不放），直接死锁在这：不是取消检查本身
            # 失灵，是子任务压根排不上队去做那个检查，"停止"按下去也没用，线程再也
            # 不会结束（实测复现过：全部请求持续失败时会稳定卡死，不是偶发）。这里
            # 先让 `async with semaphore` 正常退出、名额还回去，再递归。
            await _bisect_batch(batch, count_progress)
            return

        assert raw is not None
        parsed = prompt_strategy.parse_batch_response(raw, len(batch))
        if parsed is None:
            if on_log is not None:
                on_log(f"批次回复解析失败（{len(batch)} 条），拆分重试")
            await _bisect_batch(batch, count_progress)
            return

        fallback_jobs: list[Job] = []
        for i, job in enumerate(batch, start=1):
            item_text = parsed[i]
            if _looks_like_leaked_context(
                job.protected_text, item_text
            ) or not _has_all_placeholders(item_text, job.mapping):
                # 批量回复整体格式没问题，但这一条自己漏了控制码占位符、或者夹带了
                # 上下文内容——只把这一条退化成单独调用重问，其它条目已经解析正确
                # 的不用跟着陪葬。
                fallback_jobs.append(job)
                continue
            translated_text = restore(item_text, job.mapping)
            store.set_memory(compute_source_hash(job.source_text), job.source_text, translated_text)
            _write_result(job.group, job.result_prefix + translated_text, count_progress)

        if fallback_jobs:
            await asyncio.gather(
                *(_translate_single_job(job, count_progress) for job in fallback_jobs)
            )

    batches = _chunk_jobs_by_group(jobs, batch_size)
    await asyncio.gather(*(_translate_batch(batch) for batch in batches))

    jobs_by_source = {job.source_text: job for job in jobs}
    for round_idx in range(auto_retry_rounds):
        if not failures or _cancelled():
            break
        if on_log is not None:
            on_log(f"第 {round_idx + 1}/{auto_retry_rounds} 轮自动重试：{len(failures)} 条待重试")
        await _interruptible_sleep(retry_wait_seconds, _cancelled)
        if _cancelled():
            break
        retry_jobs = [jobs_by_source[source_text] for source_text, _error in failures]
        failures.clear()
        retry_batches = _chunk_jobs_by_group(retry_jobs, batch_size)
        await asyncio.gather(
            *(_translate_batch(batch, count_progress=False) for batch in retry_batches)
        )

    return failures
