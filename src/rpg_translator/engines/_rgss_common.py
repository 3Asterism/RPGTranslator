"""VX Ace / XP / VX（RGSS1〜3）共通のヘルパー。どれも Ruby Marshal + `code`/`parameters`
形式のイベントコマンドという同じ骨格を共有しているため、ここにまとめている。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, ClassVar

from rubymarshal.classes import RubyObject

from rpg_translator.codec.rvdata2_codec import read_rvdata2, write_rvdata2
from rpg_translator.core.ir import EngineName, TextUnit, compute_text_unit_id
from rpg_translator.engines.base import EngineAdapter, copy_project_if_different

_PURE_TAG_NOTE_RE = re.compile(r"^(\s*<[^<>\r\n]+>\s*)+$")

# VX Ace 默认消息框（544px 宽，减去左右留白，默认字号 22）大约能塞下这么多"半角宽度单位"
# 每行——这是没有真机可测下的估算值，不是精确像素计算，仅用于 spec 9.2.a 的简单重新分行
# 方案；真要精确必须走 9.2.b 的运行时像素宽度补丁。
DEFAULT_LINE_WIDTH_UNITS = 24


def _char_width(ch: str) -> int:
    """中日韩全角字符按 2 倍宽度估算，其余（含半角假名、英文数字）按 1 倍。"""
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def rewrap_paragraph(text: str, line_count: int, max_width: int = DEFAULT_LINE_WIDTH_UNITS) -> list[str]:
    """把 text 按估算宽度贪心重新分行，塞进恰好 line_count 行。

    最后一行如果还装不下剩余文本，宁可超宽（游戏内可能视觉裁切）也不截断丢字——
    丢译文比溢出更糟，溢出至少玩家还能看到大部分内容。
    """
    line_count = max(line_count, 1)
    chars = list(text.replace("\r\n", "\n").replace("\n", ""))

    lines: list[str] = []
    current = ""
    current_width = 0
    i = 0
    while i < len(chars):
        if len(lines) == line_count - 1:
            current += "".join(chars[i:])
            break
        ch = chars[i]
        w = _char_width(ch)
        if current and current_width + w > max_width:
            lines.append(current)
            current = ""
            current_width = 0
            continue
        current += ch
        current_width += w
        i += 1
    lines.append(current)

    while len(lines) < line_count:
        lines.append("")
    return lines


def is_pure_tag_note(text: str) -> bool:
    return bool(_PURE_TAG_NOTE_RE.match(text))


def rv_get(obj: Any, key: Any) -> Any:
    if isinstance(obj, RubyObject):
        return obj.attributes[key]
    return obj[key]


def rv_set(obj: Any, key: Any, value: Any) -> None:
    if isinstance(obj, RubyObject):
        obj.attributes[key] = value
    else:
        obj[key] = value


def _decode_rv_bytes(data: bytes) -> str:
    """XP/VX 用的老版本 Ruby（1.8，字符串没有编码感知）marshal 出来的字符串，
    `rubymarshal` 不会像 VX Ace（Ruby 1.9+，字符串统一带 ivar 编码标记）那样
    自动解码，原样是 `bytes`——真机验证过（用一个真实 RPG Maker XP 工程实测）
    之前这里直接对 bytes 调用 Python 内置 `str()`，抽出来的"文本"其实是
    `b'...'` 这种 repr 字面量，完全不能用。跟 wolf_binary.py 一样先试 UTF-8
    再退回 cp932，覆盖"现代编辑器存的 UTF-8 字节"和"经典日文 XP 工程的
    Shift-JIS 字节"两种真实情况。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp932")


def rv_str(value: Any) -> str:
    """从 rubymarshal 属性值安全取文本：`bytes` 走 `_decode_rv_bytes`，
    其他类型（已经是 `str`，或 VX Ace 那种已解码好的情况）走普通 `str()`。"""
    return _decode_rv_bytes(value) if isinstance(value, bytes) else str(value)


def _encode_like(original: Any, text: str) -> Any:
    """回填时如果这个槽位原本是 `bytes`（老版本 Ruby 字符串），要编码回
    `bytes` 写回去，不能留 Python `str`——不然重新序列化出来的 Marshal
    字节码格式会跟原版不一致。用跟读取时相同的编码探测顺序（先 UTF-8 后
    cp932）保证同一个字符串来回一致，不会读的时候当 UTF-8、写的时候却
    编成 cp932。"""
    if not isinstance(original, bytes):
        return text
    try:
        original.decode("utf-8")
        return text.encode("utf-8")
    except UnicodeDecodeError:
        return text.encode("cp932")


def parse_locator(locator: str) -> list[Any]:
    segments: list[Any] = []
    for seg in locator.split("/"):
        if seg.startswith("@"):
            segments.append(seg)
        elif seg.lstrip("-").isdigit():
            segments.append(int(seg))
        else:
            segments.append(seg)
    return segments


def locator_get(root: Any, locator: str) -> Any:
    cur = root
    for seg in parse_locator(locator):
        cur = rv_get(cur, seg)
    return cur


def locator_set(root: Any, locator: str, value: str) -> None:
    segments = parse_locator(locator)
    cur = root
    for seg in segments[:-1]:
        cur = rv_get(cur, seg)
    last = segments[-1]
    current = rv_get(cur, last)
    rv_set(cur, last, _encode_like(current, value))


class PendingUnit:
    __slots__ = ("locator", "source_text", "context_group", "extra_locators")

    def __init__(
        self,
        locator: str,
        source_text: str,
        context_group: str,
        extra_locators: list[str] | None = None,
    ):
        self.locator = locator
        self.source_text = source_text
        self.context_group = context_group
        self.extra_locators = extra_locators or []


def extract_command_list(
    commands: list[RubyObject], path_prefix: str, group: str
) -> list[PendingUnit]:
    found: list[PendingUnit] = []
    i = 0
    n = len(commands)
    while i < n:
        cmd = commands[i]
        code = cmd.attributes.get("@code")
        params = cmd.attributes.get("@parameters", [])
        if code == 401:
            # VX Ace/XP/VX のメッセージ窓は自動改行しない固定 4 行仕様なので、同じ
            # Show Text 命令が続く行は合体させて 1 段落として翻訳に出す（逐行翻訳しない、
            # spec 第 9 節）。inject 側で訳文を推定幅で再改行して元の行数に配り直す。
            run_start = i
            lines: list[str] = []
            locators: list[str] = []
            while i < n:
                run_cmd = commands[i]
                if run_cmd.attributes.get("@code") != 401:
                    break
                run_params = run_cmd.attributes.get("@parameters", [])
                lines.append(rv_str(run_params[0]) if run_params else "")
                locators.append(f"{path_prefix}/{i}/@parameters/0")
                i += 1
            source_text = "\n".join(lines)
            if source_text.strip():
                found.append(
                    PendingUnit(locators[0], source_text, group, extra_locators=locators[1:])
                )
            continue
        if code == 405:
            # Show Scrolling Text はスクロール表示で 4 行固定枠の制限が無いため、
            # 401 と違い合体させず 1 行ずつ独立した TextUnit のまま扱う。
            if params and rv_str(params[0]):
                found.append(PendingUnit(f"{path_prefix}/{i}/@parameters/0", rv_str(params[0]), group))
        elif code == 102:
            choices = params[0] if params else []
            for ci, choice in enumerate(choices):
                if rv_str(choice):
                    found.append(
                        PendingUnit(f"{path_prefix}/{i}/@parameters/0/{ci}", rv_str(choice), group)
                    )
        elif code == 320:
            if len(params) > 1 and rv_str(params[1]):
                found.append(PendingUnit(f"{path_prefix}/{i}/@parameters/1", rv_str(params[1]), group))
        # code 101 はヘッダーのみで話者名パラメータなし（MZ 独自機能）
        # 108/408 (Comment)・355/655 (Script) はデフォルトで無視（MV/MZ と同じ方針）
        i += 1
    return found


DATABASE_TEXT_FIELDS = ["@name", "@nickname", "@description", "@note", "@message1", "@message2"]
MAP_FILE_RE = re.compile(r"^Map\d{3}\.")


class RGSSAdapterBase(EngineAdapter):
    """VX Ace / XP / VX 共通の extract/inject 実装。サブクラスは engine_name・data_dir・
    file_extension・database_files・detect() だけ定義すればいい。"""

    engine_name: ClassVar[EngineName]
    data_dir: ClassVar[str] = "Data"
    file_extension: ClassVar[str]
    database_files: ClassVar[list[str]]

    def extract(self, project_dir: Path) -> list[TextUnit]:
        data_root = project_dir / self.data_dir
        pending: list[PendingUnit] = []

        for map_file in sorted(data_root.glob(f"Map*{self.file_extension}")):
            if not MAP_FILE_RE.match(map_file.name):
                continue
            rel_path = f"{self.data_dir}/{map_file.name}"
            game_map = read_rvdata2(map_file)
            events = game_map.attributes.get("@events", {})
            for event_id, event in events.items():
                if event is None:
                    continue
                pages = event.attributes.get("@pages", [])
                for page_idx, page in enumerate(pages):
                    group = f"{rel_path}:@events/{event_id}/@pages/{page_idx}"
                    path_prefix = f"@events/{event_id}/@pages/{page_idx}/@list"
                    pending.extend(
                        extract_command_list(page.attributes.get("@list", []), path_prefix, group)
                    )

        common_events_file = data_root / f"CommonEvents{self.file_extension}"
        if common_events_file.is_file():
            rel_path = f"{self.data_dir}/CommonEvents{self.file_extension}"
            common_events = read_rvdata2(common_events_file)
            for ce_idx, ce in enumerate(common_events):
                if ce is None:
                    continue
                group = f"{rel_path}:{ce_idx}"
                path_prefix = f"{ce_idx}/@list"
                pending.extend(
                    extract_command_list(ce.attributes.get("@list", []), path_prefix, group)
                )

        units = self._pending_to_units(pending)

        for db_filename in self.database_files:
            db_file = data_root / db_filename
            if not db_file.is_file():
                continue
            rel_path = f"{self.data_dir}/{db_filename}"
            records = read_rvdata2(db_file)
            units.extend(self._extract_database_file(records, rel_path))

        return units

    def _extract_database_file(self, records: list[Any], rel_path: str) -> list[TextUnit]:
        units: list[TextUnit] = []
        for idx, record in enumerate(records):
            if record is None or not isinstance(record, RubyObject):
                continue
            record_name = rv_str(record.attributes.get("@name", ""))
            for field in DATABASE_TEXT_FIELDS:
                if field not in record.attributes:
                    continue
                text = rv_str(record.attributes[field])
                if not text.strip():
                    continue
                if field == "@note" and is_pure_tag_note(text):
                    continue
                locator = f"{idx}/{field}"
                context = "" if field == "@name" else f"数据库记录：{record_name}"
                units.append(
                    TextUnit(
                        id=compute_text_unit_id(self.engine_name, rel_path, locator),
                        engine=self.engine_name,
                        file_path=rel_path,
                        locator=locator,
                        context=context,
                        source_text=text,
                    )
                )
        return units

    def _pending_to_units(self, pending: list[PendingUnit]) -> list[TextUnit]:
        # 不再把同页其它台词整段拼进 context（页面越长开销越是平方级）——改成只带一个
        # 分组 id，交给 batch_translator 把同一分组的台词打包进同一次请求整体翻译，
        # 上下文靠"同一次请求里的其它行"自然获得（调研见 CLAUDE.md）。
        units: list[TextUnit] = []
        for p in pending:
            file_path = p.context_group.split(":", 1)[0]
            units.append(
                TextUnit(
                    id=compute_text_unit_id(self.engine_name, file_path, p.locator),
                    engine=self.engine_name,
                    file_path=file_path,
                    locator=p.locator,
                    context="",
                    context_group=p.context_group,
                    source_text=p.source_text,
                    extra_locators=p.extra_locators,
                )
            )
        return units

    def inject(self, project_dir: Path, units: list[TextUnit], output_dir: Path) -> None:
        copy_project_if_different(project_dir, output_dir)

        by_file: dict[str, list[TextUnit]] = {}
        for unit in units:
            by_file.setdefault(unit.file_path, []).append(unit)

        for rel_path, file_units in by_file.items():
            full_path = output_dir / rel_path
            root = read_rvdata2(full_path)
            for unit in file_units:
                if unit.extra_locators:
                    slot_locators = [unit.locator, *unit.extra_locators]
                    if unit.translated_text is not None:
                        lines = rewrap_paragraph(unit.translated_text, len(slot_locators))
                    else:
                        # 还没翻译：原样按原始换行拆回各行，保证未翻译回填和原工程
                        # 逐字节一致（M1/M4 的回归校验），不能套重新分行逻辑。
                        lines = unit.source_text.split("\n")
                        if len(lines) < len(slot_locators):
                            lines += [""] * (len(slot_locators) - len(lines))
                        elif len(lines) > len(slot_locators):
                            extra = "\n".join(lines[len(slot_locators) - 1 :])
                            lines = lines[: len(slot_locators) - 1] + [extra]
                    for slot_locator, line in zip(slot_locators, lines):
                        locator_set(root, slot_locator, line)
                else:
                    value = (
                        unit.translated_text if unit.translated_text is not None else unit.source_text
                    )
                    locator_set(root, unit.locator, value)
            write_rvdata2(full_path, root)

        self._after_inject(output_dir, units)

    def _after_inject(self, output_dir: Path, units: list[TextUnit]) -> None:
        """子类可选覆盖的收尾钩子，写完所有 TextUnit 之后调用一次。
        默认什么都不做（目前只有 VXAceAdapter 用它挂运行时像素换行补丁）。"""
