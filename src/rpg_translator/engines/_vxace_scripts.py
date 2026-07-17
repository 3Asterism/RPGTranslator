"""VX Ace `Data/Scripts.rvdata2` 专用读写——不走 `rvdata2_codec.py` 的通用
`rubymarshal` 路径。

真机验证过程中发现：真实工程（不是手搭的合成样例）里 Scripts.rvdata2 每个
脚本条目的第三个字段（zlib 压缩后的脚本源码字节）在 Marshal 里被打上了
`E:true`（"按 UTF-8 解码"）标记，尽管内容其实是任意二进制 zlib 数据——这
是 RPG Maker 编辑器自己序列化时的固有写法，不是这份工程写坏了。`rubymarshal`
库读到这种字符串会强制按 UTF-8/`unicode-escape` 解码，直接崩溃。生产的
extract/inject 流程本来就不碰 Scripts.rvdata2（spec 里 v1 明确不翻译脚本
正文），但 VX Ace 像素级换行补丁（spec 9.2.b）需要往这个文件里追加一个新
脚本条目、以及扫描现有脚本名判断是否有第三方消息系统脚本——这两件事都需要
先能把这个文件正确读进来。

策略：只解析到刚好够用的程度。每个脚本条目在顶层数组里是
`[id: Fixnum, name: String, data: String]` 这个固定形状，本模块用一个只
认识 Marshal 里会在这个文件出现的几种类型（nil/true/false/Fixnum/Symbol
定义与反向引用/String(+ivar)/Array）的小型手写游标解析器把每个条目在原始
字节流里的起止位置切出来，只把 `name` 解码成文本用于关键词扫描，`data`
原样按字节切片保留、不做任何解码——现有的 126 个（示例工程的数字）条目
因此可以逐字节透传，插入新脚本时不会有任何机会破坏它们。
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path

_MARSHAL_HEADER = b"\x04\x08"

_TAG_NIL = ord("0")
_TAG_TRUE = ord("T")
_TAG_FALSE = ord("F")
_TAG_FIXNUM = ord("i")
_TAG_SYMBOL = ord(":")
_TAG_SYMBOL_LINK = ord(";")
_TAG_IVAR = ord("I")
_TAG_STRING = ord('"')
_TAG_ARRAY = ord("[")
_TAG_OBJECT_LINK = ord("@")


class ScriptsFormatError(Exception):
    """Scripts.rvdata2 的字节内容不符合本模块认识的固定形状。"""


@dataclass
class ScriptEntry:
    id: int
    name: str
    # 压缩后的原始字节，未解压——绝大多数条目对本模块来说是透传数据，不需要
    # 解压内容也能完成"扫描名字找冲突脚本 / 追加一个新脚本"这两件事。
    compressed_source: bytes


class _Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read_byte(self) -> int:
        if self.pos >= len(self.data):
            raise ScriptsFormatError(f"unexpected end of data at offset {self.pos}")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def read(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.data):
            raise ScriptsFormatError(f"unexpected end of data at offset {self.pos} (need {n} bytes)")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def read_long(self) -> int:
        """Ruby Marshal 变长整数编码。"""
        c = self.read_byte()
        signed_c = c - 256 if c >= 128 else c
        if signed_c == 0:
            return 0
        if 5 <= signed_c <= 127:
            return signed_c - 5
        if -128 <= signed_c <= -5:
            return signed_c + 5
        if 1 <= signed_c <= 4:
            raw = self.read(signed_c)
            return int.from_bytes(raw, "little", signed=False)
        if -4 <= signed_c <= -1:
            raw = self.read(-signed_c)
            value = int.from_bytes(raw, "little", signed=False)
            return value - (1 << (8 * -signed_c))
        raise ScriptsFormatError(f"unreachable long-encoding byte {c:#x}")


def _skip_value(c: _Cursor) -> None:
    """跳过一个 Marshal 值（不关心内容），只认识这个文件会出现的类型，
    遇到不认识的类型直接报错——比静默跳过瞎猜安全。"""
    tag = c.read_byte()
    if tag in (_TAG_NIL, _TAG_TRUE, _TAG_FALSE):
        return
    if tag == _TAG_FIXNUM:
        c.read_long()
        return
    if tag == _TAG_SYMBOL:
        length = c.read_long()
        c.read(length)
        return
    if tag == _TAG_SYMBOL_LINK:
        c.read_long()
        return
    if tag == _TAG_OBJECT_LINK:
        c.read_long()
        return
    if tag == _TAG_STRING:
        length = c.read_long()
        c.read(length)
        return
    if tag == _TAG_IVAR:
        _skip_value(c)  # 被包裹的实际值（这个文件里总是 String）
        ivar_count = c.read_long()
        for _ in range(ivar_count):
            _skip_value(c)  # ivar 的 key（Symbol 定义或反向引用）
            _skip_value(c)  # ivar 的 value（这个文件里总是 True/False/Symbol）
        return
    if tag == _TAG_ARRAY:
        length = c.read_long()
        for _ in range(length):
            _skip_value(c)
        return
    raise ScriptsFormatError(f"unrecognized Marshal tag {tag:#x} at offset {c.pos - 1}")


def _read_string_value(c: _Cursor) -> bytes:
    """读一个 String（可能带 IVAR 编码标记外壳），只要原始字节内容，
    外壳的编码标记（E:true 之类）读过就丢，不解码。"""
    tag = c.read_byte()
    if tag == _TAG_STRING:
        length = c.read_long()
        return c.read(length)
    if tag == _TAG_IVAR:
        inner_tag = c.read_byte()
        if inner_tag != _TAG_STRING:
            raise ScriptsFormatError(f"expected IVAR-wrapped String, got inner tag {inner_tag:#x}")
        length = c.read_long()
        content = c.read(length)
        ivar_count = c.read_long()
        for _ in range(ivar_count):
            _skip_value(c)
            _skip_value(c)
        return content
    raise ScriptsFormatError(f"expected String (with or without IVAR wrapper), got tag {tag:#x}")


def read_scripts(path: Path) -> list[ScriptEntry]:
    data = path.read_bytes()
    if not data.startswith(_MARSHAL_HEADER):
        raise ScriptsFormatError(f"{path}: missing Marshal 4.8 header")
    c = _Cursor(data)
    c.pos = len(_MARSHAL_HEADER)
    tag = c.read_byte()
    if tag != _TAG_ARRAY:
        raise ScriptsFormatError(f"{path}: expected top-level Array, got tag {tag:#x}")
    entry_count = c.read_long()

    entries: list[ScriptEntry] = []
    for i in range(entry_count):
        entry_tag = c.read_byte()
        if entry_tag != _TAG_ARRAY:
            raise ScriptsFormatError(f"{path}: entry {i}: expected Array, got tag {entry_tag:#x}")
        field_count = c.read_long()
        if field_count != 3:
            raise ScriptsFormatError(f"{path}: entry {i}: expected 3 fields, got {field_count}")
        id_tag = c.read_byte()
        if id_tag != _TAG_FIXNUM:
            raise ScriptsFormatError(f"{path}: entry {i}: expected Fixnum id, got tag {id_tag:#x}")
        script_id = c.read_long()
        name_bytes = _read_string_value(c)
        data_bytes = _read_string_value(c)
        try:
            name = name_bytes.decode("utf-8")
        except UnicodeDecodeError:
            name = name_bytes.decode("cp932")
        entries.append(ScriptEntry(script_id, name, data_bytes))

    if c.pos != len(data):
        raise ScriptsFormatError(f"{path}: {len(data) - c.pos} trailing bytes after last script entry")
    return entries


def _write_long(value: int) -> bytes:
    if value == 0:
        return bytes([0])
    if 0 < value <= 122:
        return bytes([value + 5])
    if -123 <= value < 0:
        return bytes([(value - 5) & 0xFF])
    for n in (1, 2, 3, 4):
        limit = 1 << (8 * n)
        if 0 < value < limit:
            return bytes([n]) + value.to_bytes(n, "little")
        if -limit <= value < 0:
            return bytes([(-n) & 0xFF]) + (value + limit).to_bytes(n, "little")
    raise ScriptsFormatError(f"integer {value} out of range for Marshal long encoding")


def _write_string_with_utf8_flag(content: bytes) -> bytes:
    """`I"<len><bytes>` + 1 个 ivar `:E => true`——和这份文件里其余条目
    同款写法（哪怕 content 是压缩后的二进制，也照样打 E:true，见模块文档）。"""
    return (
        bytes([_TAG_IVAR, _TAG_STRING])
        + _write_long(len(content))
        + content
        + _write_long(1)
        + bytes([_TAG_SYMBOL])
        + _write_long(1)
        + b"E"
        + bytes([_TAG_TRUE])
    )


def encode_new_entry(script_id: int, name: str, source: str) -> bytes:
    """把一个新脚本编码成一个可以直接追加进顶层数组的 `[id, name, data]`
    三元组字节块（data 是 `source` 的 zlib 压缩结果）。"""
    compressed = zlib.compress(source.encode("utf-8"))
    return (
        bytes([_TAG_ARRAY])
        + _write_long(3)
        + bytes([_TAG_FIXNUM])
        + _write_long(script_id)
        + _write_string_with_utf8_flag(name.encode("utf-8"))
        + _write_string_with_utf8_flag(compressed)
    )


def append_script(path: Path, script_id: int, name: str, source: str) -> None:
    """往 Scripts.rvdata2 追加一个新脚本条目并写回。已有条目的原始字节
    原样透传（不重新编码），保证不会因为重新序列化而意外改动它们。"""
    data = path.read_bytes()
    if not data.startswith(_MARSHAL_HEADER):
        raise ScriptsFormatError(f"{path}: missing Marshal 4.8 header")
    header_end = len(_MARSHAL_HEADER)
    if data[header_end] != _TAG_ARRAY:
        raise ScriptsFormatError(f"{path}: expected top-level Array")

    c = _Cursor(data)
    c.pos = header_end + 1
    length_start = c.pos
    entry_count = c.read_long()
    length_end = c.pos

    new_entry = encode_new_entry(script_id, name, source)
    new_data = (
        data[:length_start]
        + _write_long(entry_count + 1)
        + data[length_end:]
        + new_entry
    )
    path.write_bytes(new_data)


def has_conflicting_message_system(entries: list[ScriptEntry]) -> str | None:
    """脚本名命中已知第三方消息系统关键词就返回那个脚本名，没命中返回
    None。命中就该跳过像素换行补丁注入，降级用估算重排方案（见
    galgame_rpgmaker_translator_spec 第 9 节的设计决策）。"""
    keywords = (
        "yea",
        "yanfly",
        "galv",
        "luna",
        "mog",
        "message system",
        "メッセージシステム",
    )
    for entry in entries:
        lowered = entry.name.lower()
        if any(k in lowered for k in keywords):
            return entry.name
    return None
