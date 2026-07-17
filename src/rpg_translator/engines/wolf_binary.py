"""Low-level binary parser/serializer for WOLF RPG Editor (ウディタ) project files.

RESEARCH PROVENANCE / HOW THIS WAS VERIFIED
============================================
WOLF RPG Editor's .dat/.project/.mps formats have no official documentation.
This module was ported after cross-reading three independent community
implementations (each an independent reverse-engineering effort -- this is
triangulated, not "trust one source"):

  - elizagamedev/wolftrans (Ruby, MPL-2.0, ~2016, the original reverse-
    engineering effort; other tools cite it as their reference)
    https://github.com/elizagamedev/wolftrans
  - KCFindstr/rewolf-trans (TypeScript, ~2019-2021)
    https://github.com/KCFindstr/rewolf-trans
  - Sinflower/WolfTL (C++, MIT, actively maintained 2024-2026 -- the most
    complete of the three; its README claims tested support for "WolfPro"
    protected games)
    https://github.com/Sinflower/WolfTL

The three agree byte-for-byte on the core "classic"/unprotected format (magic
numbers, string encoding, event/page/command framing, database
type/field/data layout). Where they disagree (see the ENCRYPTION note below)
this module says so explicitly instead of silently picking one.

wolftrans is MPL-2.0. No wolftrans source was copied verbatim here -- this is
an independent Python port of the *file format* (which is not itself
copyrightable), cross-checked against the MIT-licensed WolfTL and the
unlicensed-but-public rewolf-trans for agreement. This notice exists because
the porting effort leaned on wolftrans's class/field layout as the reference
structure, and MPL-2.0 asks that derived files carry a source + license note.

WHAT IS **NOT** VERIFIED
=========================
No real WOLF RPG Editor project was available for this milestone (unlike the
VX Ace/XP/VX adapters, which at least had rubymarshal to lean on -- WOLF has
no synthetic-fixture precedent in this repo either, and no free/official
sample project was located during this research pass). Everything below was
derived from reading the three tools' source and hand-building a synthetic
fixture that matches their documented byte layout
(tests/conftest.py's build_wolf_project()). It has never been run against an
actual WOLF-authored file. Treat this the same way vxace.py's own header
treats its field names: internally consistent and triangulated, not
field-proven -- re-verify against a real project before trusting this on a
game you care about.

SCOPE -- what this module parses
=================================
  - Data/MapData/**/*.mps           (WolfMap: tileset/width/height/events/
    pages/commands)
  - Data/BasicData/*.project + matching *.dat (WolfDatabase: the
    type/field/data schema-plus-values pattern used for Actors/Items/etc.
    -equivalent tables)
  - Data/BasicData/CommonEvent.dat  (WolfCommonEvents: shared/common event
    scripts -- where a large fraction of real WOLF game dialogue tends to
    live)
  - String encoding: cp932 (a Shift-JIS variant) before editor v2.2, UTF-8
    from v2.2 onward. CORRECTION vs. this project's spec section 6.4 (which
    is itself labeled secondhand/to-be-reverified): encoding is not actually
    picked by "probing a version number" field. Every format's magic-number
    header has one designated byte that reads 0x00 for cp932 builds and 0x55
    ('U') for UTF-8 builds (see WolfTL's `MagicNumber.utf8Idx`); this module
    checks that one byte per file, not a version integer, to choose cp932 vs.
    utf-8.

SCOPE -- explicitly NOT handled (known gaps, not oversights)
================================================================
  1. "WolfPro" AES-protected releases. WOLF RPG Editor's paid/Pro tier can
     encrypt Data files with escalating schemes across editor versions (v3.1
     adds a custom key blob, v3.3 uses AES-CTR keyed via a Mersenne-Twister-
     seeded RNG chain, v3.5 layers a SHA-512 password/salt derivation on top).
     WolfTL (C++) has ported all of these, proving it is *possible* -- but
     porting that much bespoke crypto (MT19937 + AES key schedule/CTR +
     SHA-512, none of which have a stdlib-only Python equivalent verified to
     match bit-for-bit) is a substantially larger, harder-to-verify-without-
     real-protected-files effort than fits this milestone. `read()` raises a
     clear, actionable WolfFormatError naming "WolfPro"/encryption instead of
     attempting to guess or silently emitting garbage.
  2. The simpler "classic" per-file XOR/LCG cipher (used for lightly-
     protected, pre-Pro projects) is understood in principle -- it is a
     MSVC-rand-style linear congruential generator XORed byte-by-byte, and
     two of the three reference tools agree on the algorithm byte-for-byte.
     It is deliberately NOT implemented here, because the three tools
     disagree on whether/how it even applies to Database .dat files
     specifically (wolftrans's Ruby applies it to Database.dat; WolfTL's
     newer C++ `FileCoder::load()` appears to skip the classic-XOR call
     specifically for `WolfFileType::DataBase`). Given real disagreement
     between the only two available references and no sample file to
     adjudicate with, guessing would risk silently-wrong output, which is
     worse than a clear refusal. Any file whose first byte is not 0x00 is
     treated as "encrypted, unsupported" and raises rather than being parsed.
  3. Game.dat is not parsed at all. It carries mostly non-dialogue metadata
     (window title, font names, a large trailing "unknown" blob even the
     reference tools admit they don't fully understand) and contributes
     little translatable text; skipping it keeps this module's
     synthetic-fixture surface smaller without materially reducing
     translation coverage.
  4. Newer-editor Map "page" layout: WolfTL's C++ Page parser (2024-2026)
     reads an extra `features` int32 and a conditional `page_transfer` byte
     after the command list that the older wolftrans/rewolf-trans parsers do
     not have -- evidence the on-disk Page format itself changed across
     editor versions. This module targets the older/simpler layout (the one
     cross-validated by two independent implementations, and the one gated
     by the format-version marker byte baked into `_MAP_HEADER_PREFIX`).
     WolfTL also gates LZ4-compressed maps behind that same marker byte, so a
     newer-format map file will fail loudly here (via the header `verify()`)
     rather than silently mis-parsing.
  5. Field-type edge case: WolfTL's Database `Type::ReadDat` has a branch for
     `unknown1 == STRING_INDICATOR (0x0001D4C0)` that reads one extra string;
     wolftrans's Ruby parser has no equivalent. This is a rare sentinel value
     unlikely to appear in an ordinary user-authored database and is not
     handled here; if parsing raises on a real project's DataBase.dat, this
     is the first place to look.

GO / NO-GO CALL FOR M4.8
=========================
GO for unencrypted WOLF projects using the older/common Map page layout --
which is the expected shape of a developer's own editable project directory,
since WolfPro protection is an opt-in publish-time feature. NO-GO (loud,
explicit failure, never a silently-empty result) for WolfPro-protected
releases, for classic-cipher-protected files, and for anything that doesn't
match the byte layout documented above.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

CP932 = "cp932"
UTF8 = "utf-8"


class WolfFormatError(Exception):
    """Raised when a WOLF file doesn't match the expected (unencrypted,
    classic-layout) structure this module supports -- including when it
    looks like a WolfPro-protected or classic-XOR-protected file (see the
    module docstring's SCOPE section, gaps 1-2)."""


# ---------------------------------------------------------------------------
# Byte-level reader / writer
# ---------------------------------------------------------------------------


class ByteReader:
    def __init__(self, data: bytes, encoding: str = CP932):
        self._data = data
        self._pos = 0
        self.encoding = encoding

    def eof(self) -> bool:
        return self._pos >= len(self._data)

    def tell(self) -> int:
        return self._pos

    def read(self, size: int) -> bytes:
        end = self._pos + size
        if size < 0 or end > len(self._data):
            raise WolfFormatError(
                f"unexpected end of data at offset {self._pos} (need {size} bytes, "
                f"have {len(self._data) - self._pos})"
            )
        chunk = self._data[self._pos : end]
        self._pos = end
        return chunk

    def read_byte(self) -> int:
        return self.read(1)[0]

    def read_int(self) -> int:
        (value,) = struct.unpack_from("<i", self.read(4))
        return value

    def read_string(self) -> str:
        size = self.read_int()
        if size <= 0:
            raise WolfFormatError(f"string length must be positive, got {size} at offset {self._pos}")
        data = self.read(size - 1)
        terminator = self.read_byte()
        if terminator != 0:
            raise WolfFormatError(f"string not null-terminated at offset {self._pos}")
        try:
            return data.decode(self.encoding)
        except UnicodeDecodeError as exc:
            raise WolfFormatError(
                f"failed to decode string as {self.encoding} at offset {self._pos}: {exc}"
            ) from exc

    def verify(self, expected: bytes) -> None:
        got = self.read(len(expected))
        if got != expected:
            raise WolfFormatError(
                f"magic mismatch at offset {self._pos - len(expected)}: expected {expected!r}, got {got!r}"
            )

    def verify_magic_utf8_aware(self, magic_cp932: bytes, utf8_index: int) -> bool:
        """Reads len(magic_cp932) bytes and checks them against either the
        cp932 magic or its UTF-8 variant (same bytes except `utf8_index` is
        0x55 ('U') instead of 0x00). Sets self.encoding accordingly and
        returns whether the file is UTF-8. Raises WolfFormatError -- with a
        hint that this may be a WolfPro-protected file -- if neither matches.
        """
        got = self.read(len(magic_cp932))
        if got == magic_cp932:
            self.encoding = CP932
            return False
        utf8_variant = bytearray(magic_cp932)
        utf8_variant[utf8_index] = 0x55
        if bytes(utf8_variant) == got:
            self.encoding = UTF8
            return True
        raise WolfFormatError(
            f"unrecognized header (expected {magic_cp932!r} or its UTF-8 variant, got {got!r}); "
            "this usually means the file is WolfPro-protected or otherwise encrypted, which this "
            "adapter does not support (see wolf_binary.py's module docstring)"
        )


class ByteWriter:
    def __init__(self, encoding: str = CP932):
        self._buf = bytearray()
        self.encoding = encoding

    def getvalue(self) -> bytes:
        return bytes(self._buf)

    def write(self, data: bytes) -> None:
        self._buf.extend(data)

    def write_byte(self, value: int) -> None:
        self._buf.append(value & 0xFF)

    def write_int(self, value: int) -> None:
        self._buf.extend(struct.pack("<i", value))

    def write_string(self, value: str) -> None:
        encoded = value.encode(self.encoding)
        self.write_int(len(encoded) + 1)
        self.write(encoded)
        self.write_byte(0)


def _check_not_encrypted(data: bytes, path_for_error: object) -> None:
    if not data:
        raise WolfFormatError(f"{path_for_error}: empty file")
    if data[0] != 0:
        raise WolfFormatError(
            f"{path_for_error}: first byte is {data[0]:#x} (expected 0x00), which means this file "
            "is encrypted -- either WolfPro AES protection or the classic XOR cipher. Neither is "
            "supported by this adapter (see wolf_binary.py's module docstring, SCOPE gaps 1-2)."
        )


# ---------------------------------------------------------------------------
# Shared: event commands + movement routes (used by Map pages and
# CommonEvents alike)
# ---------------------------------------------------------------------------

_ROUTE_TERMINATOR = bytes([0x01, 0x00])

CID_MESSAGE = 101
CID_CHOICES = 102


@dataclass
class RouteCommand:
    """A single movement-route instruction. Semantics unknown/opaque (no
    reference tool decodes these further than id+args) -- carried through
    verbatim for round-trip fidelity."""

    command_id: int
    args: list[int]

    @classmethod
    def read(cls, r: ByteReader) -> "RouteCommand":
        command_id = r.read_byte()
        count = r.read_byte()
        args = [r.read_int() for _ in range(count)]
        r.verify(_ROUTE_TERMINATOR)
        return cls(command_id, args)

    def write(self, w: ByteWriter) -> None:
        w.write_byte(self.command_id)
        w.write_byte(len(self.args))
        for a in self.args:
            w.write_int(a)
        w.write(_ROUTE_TERMINATOR)


@dataclass
class MoveExtra:
    """Extra payload embedded in a "Move" event command (cid 201) --
    5 opaque bytes + a flags byte + an embedded route-command list, present
    only when the command's terminator byte is 0x01 instead of 0x00."""

    unknown: list[int]
    flags: int
    route: list[RouteCommand]

    @classmethod
    def read(cls, r: ByteReader) -> "MoveExtra":
        unknown = [r.read_byte() for _ in range(5)]
        flags = r.read_byte()
        route = [RouteCommand.read(r) for _ in range(r.read_int())]
        return cls(unknown, flags, route)

    def write(self, w: ByteWriter) -> None:
        for b in self.unknown:
            w.write_byte(b)
        w.write_byte(self.flags)
        w.write_int(len(self.route))
        for cmd in self.route:
            cmd.write(w)


@dataclass
class Command:
    """A single event command. All WOLF event commands share one binary
    frame regardless of `cid` (size byte, cid, int args, indent, string
    args, terminator) -- only the Move command (cid 201) appends extra data,
    signalled generically by the terminator byte being 0x01."""

    cid: int
    args: list[int]
    indent: int
    string_args: list[str]
    move_extra: MoveExtra | None = None

    @classmethod
    def read(cls, r: ByteReader) -> "Command":
        size = r.read_byte()
        if size < 1:
            raise WolfFormatError(f"command size byte must be >= 1, got {size}")
        cid = r.read_int()
        args = [r.read_int() for _ in range(size - 1)]
        indent = r.read_byte()
        string_args = [r.read_string() for _ in range(r.read_byte())]
        terminator = r.read_byte()
        move_extra = None
        if terminator == 1:
            move_extra = MoveExtra.read(r)
        elif terminator != 0:
            raise WolfFormatError(f"unexpected command terminator {terminator:#x}")
        return cls(cid, args, indent, string_args, move_extra)

    def write(self, w: ByteWriter) -> None:
        w.write_byte(len(self.args) + 1)
        w.write_int(self.cid)
        for a in self.args:
            w.write_int(a)
        w.write_byte(self.indent)
        w.write_byte(len(self.string_args))
        for s in self.string_args:
            w.write_string(s)
        if self.move_extra is not None:
            w.write_byte(1)
            self.move_extra.write(w)
        else:
            w.write_byte(0)


def command_text_slots(cmd: Command) -> list[int]:
    """Indices into cmd.string_args that hold player-facing translatable
    text, for the command kinds this module extracts. Message (101) and
    Choices (102) are handled -- Comment (103)/DebugMessage (106)/
    SetString (122)/Picture-caption (150 text mode) are known to also carry
    text in principle (per wolftrans) but are deliberately not extracted in
    this pass, mirroring this codebase's existing convention of not
    translating comment-only commands (see mv_mz.py / _rgss_common.py)."""
    if cmd.cid == CID_MESSAGE:
        return [0] if cmd.string_args else []
    if cmd.cid == CID_CHOICES:
        return list(range(len(cmd.string_args)))
    return []


# ---------------------------------------------------------------------------
# Map (.mps)
# ---------------------------------------------------------------------------

# 10 reserved zero bytes + literal "WOLFM\0" + 4 reserved zero bytes +
# int32(0x64) [format-version marker; WolfTL gates LZ4 decompression on this
# being >= 0x65, i.e. this module only supports the un-gated "classic" value]
# + byte(0x65). What follows (not included in this fixed prefix) is a
# length-prefixed string -- an opaque "editor build stamp" that differs
# between the Japanese and English editor builds in the tools that inspired
# this port ("なし" vs "No"); rather than hardcoding either literal, this
# module reads it as a normal string and round-trips it verbatim, which
# generalizes to any stamp value.
_MAP_HEADER_PREFIX = bytes(10) + b"WOLFM\x00" + bytes(4) + struct.pack("<i", 0x64) + bytes([0x65])
_EVENT_MAGIC1 = bytes([0x39, 0x30, 0x00, 0x00])
_EVENT_MAGIC2 = bytes(4)
_EVENT_INDICATOR = 0x6F
_EVENT_FINISH_INDICATOR = 0x66
_PAGE_INDICATOR = 0x79
_PAGE_FINISH_INDICATOR = 0x70
_PAGE_TERMINATOR = 0x7A
_COMMANDS_TERMINATOR = bytes([0x03, 0x00, 0x00, 0x00])
_CONDITIONS_SIZE = 1 + 4 * 4 + 4 * 4  # 37 opaque bytes, meaning not documented by any reference tool
_MOVEMENT_SIZE = 4


@dataclass
class Page:
    unknown1: int
    graphic_name: str
    graphic_direction: int
    graphic_frame: int
    graphic_opacity: int
    graphic_render_mode: int
    conditions: bytes
    movement: bytes
    flags: int
    route_flags: int
    route: list[RouteCommand]
    commands: list[Command]
    shadow_graphic_num: int
    collision_width: int
    collision_height: int

    @classmethod
    def read(cls, r: ByteReader) -> "Page":
        unknown1 = r.read_int()
        graphic_name = r.read_string()
        graphic_direction = r.read_byte()
        graphic_frame = r.read_byte()
        graphic_opacity = r.read_byte()
        graphic_render_mode = r.read_byte()
        conditions = r.read(_CONDITIONS_SIZE)
        movement = r.read(_MOVEMENT_SIZE)
        flags = r.read_byte()
        route_flags = r.read_byte()
        route = [RouteCommand.read(r) for _ in range(r.read_int())]
        commands = [Command.read(r) for _ in range(r.read_int())]
        r.verify(_COMMANDS_TERMINATOR)
        shadow_graphic_num = r.read_byte()
        collision_width = r.read_byte()
        collision_height = r.read_byte()
        terminator = r.read_byte()
        if terminator != _PAGE_TERMINATOR:
            raise WolfFormatError(f"page terminator not {_PAGE_TERMINATOR:#x} (got {terminator:#x})")
        return cls(
            unknown1,
            graphic_name,
            graphic_direction,
            graphic_frame,
            graphic_opacity,
            graphic_render_mode,
            conditions,
            movement,
            flags,
            route_flags,
            route,
            commands,
            shadow_graphic_num,
            collision_width,
            collision_height,
        )

    def write(self, w: ByteWriter) -> None:
        w.write_int(self.unknown1)
        w.write_string(self.graphic_name)
        w.write_byte(self.graphic_direction)
        w.write_byte(self.graphic_frame)
        w.write_byte(self.graphic_opacity)
        w.write_byte(self.graphic_render_mode)
        w.write(self.conditions)
        w.write(self.movement)
        w.write_byte(self.flags)
        w.write_byte(self.route_flags)
        w.write_int(len(self.route))
        for rc in self.route:
            rc.write(w)
        w.write_int(len(self.commands))
        for c in self.commands:
            c.write(w)
        w.write(_COMMANDS_TERMINATOR)
        w.write_byte(self.shadow_graphic_num)
        w.write_byte(self.collision_width)
        w.write_byte(self.collision_height)
        w.write_byte(_PAGE_TERMINATOR)


@dataclass
class Event:
    event_id: int
    name: str
    x: int
    y: int
    pages: list[Page]

    @classmethod
    def read(cls, r: ByteReader) -> "Event":
        r.verify(_EVENT_MAGIC1)
        event_id = r.read_int()
        name = r.read_string()
        x = r.read_int()
        y = r.read_int()
        page_count = r.read_int()
        r.verify(_EVENT_MAGIC2)
        pages: list[Page] = []
        while True:
            indicator = r.read_byte()
            if indicator == _PAGE_INDICATOR:
                pages.append(Page.read(r))
            elif indicator == _PAGE_FINISH_INDICATOR:
                break
            else:
                raise WolfFormatError(f"unexpected event page indicator {indicator:#x}")
        if len(pages) != page_count:
            raise WolfFormatError(
                f"event {event_id}: page count mismatch (header said {page_count}, got {len(pages)})"
            )
        return cls(event_id, name, x, y, pages)

    def write(self, w: ByteWriter) -> None:
        w.write(_EVENT_MAGIC1)
        w.write_int(self.event_id)
        w.write_string(self.name)
        w.write_int(self.x)
        w.write_int(self.y)
        w.write_int(len(self.pages))
        w.write(_EVENT_MAGIC2)
        for p in self.pages:
            w.write_byte(_PAGE_INDICATOR)
            p.write(w)
        w.write_byte(_PAGE_FINISH_INDICATOR)


@dataclass
class WolfMap:
    tileset_id: int
    width: int
    height: int
    tiles: bytes
    events: list[Event]
    # Opaque per-file "editor build stamp" string embedded in the header
    # (see _MAP_HEADER_PREFIX's comment) -- round-tripped verbatim, content
    # not interpreted.
    header_stamp: str = ""

    @classmethod
    def read(cls, path: Path) -> "WolfMap":
        data = path.read_bytes()
        if not data:
            raise WolfFormatError(f"{path}: empty file")
        # No reference tool models an encryption/protection layer for .mps
        # files specifically (wolftrans never passes seed indices for Map;
        # WolfTL's Map-specific branch only ever checks for LZ4 compression,
        # gated by the format-version marker byte baked into the header
        # prefix below) -- so there is no encryption pre-check here, unlike
        # Database/CommonEvents.
        r = ByteReader(data)  # cp932 only -- see module docstring gap 4
        r.verify(_MAP_HEADER_PREFIX)
        header_stamp = r.read_string()
        tileset_id = r.read_int()
        width = r.read_int()
        height = r.read_int()
        r.read_int()  # event count -- redundant with len(events); recomputed on write, like wolftrans does
        tiles = r.read(width * height * 3 * 4)
        events: list[Event] = []
        while True:
            indicator = r.read_byte()
            if indicator == _EVENT_INDICATOR:
                events.append(Event.read(r))
            elif indicator == _EVENT_FINISH_INDICATOR:
                break
            else:
                raise WolfFormatError(f"{path}: unexpected event indicator {indicator:#x}")
        if not r.eof():
            raise WolfFormatError(f"{path}: file not fully parsed (trailing bytes after last event)")
        return cls(tileset_id, width, height, tiles, events, header_stamp)

    def write(self, path: Path) -> None:
        w = ByteWriter()
        w.write(_MAP_HEADER_PREFIX)
        w.write_string(self.header_stamp)
        w.write_int(self.tileset_id)
        w.write_int(self.width)
        w.write_int(self.height)
        w.write_int(len(self.events))
        w.write(self.tiles)
        for e in self.events:
            w.write_byte(_EVENT_INDICATOR)
            e.write(w)
        w.write_byte(_EVENT_FINISH_INDICATOR)
        path.write_bytes(w.getvalue())


# ---------------------------------------------------------------------------
# Database (*.project schema + matching *.dat values)
# ---------------------------------------------------------------------------

_DAT_MAGIC_CP932 = bytes([0x57, 0x00, 0x00, 0x4F, 0x4C, 0x00, 0x46, 0x4D, 0x00])
_DAT_UTF8_INDEX = 5
_DAT_DEFAULT_VERSION = 0xC1
_DAT_TYPE_SEPARATOR = bytes([0xFE, 0xFF, 0xFF, 0xFF])

_FIELD_STRING_START = 0x07D0
_FIELD_INT_START = 0x03E8


@dataclass
class Field:
    name: str
    type: int = 0
    unknown1: str = ""
    string_args: list[str] = field(default_factory=list)
    args: list[int] = field(default_factory=list)
    default_value: int = 0
    index_info: int = 0  # populated from the .dat file; encodes both is_string() and value_index()

    def is_string(self) -> bool:
        return self.index_info >= _FIELD_STRING_START

    def value_index(self) -> int:
        return self.index_info - (_FIELD_STRING_START if self.is_string() else _FIELD_INT_START)


@dataclass
class DataRecord:
    name: str
    int_values: list[int] = field(default_factory=list)
    string_values: list[str] = field(default_factory=list)


@dataclass
class DbType:
    name: str
    fields: list[Field]
    data: list[DataRecord]
    description: str
    field_type_list_size: int
    unknown1: int = 0  # per-type value from the .dat file; meaning undocumented by any reference tool

    @classmethod
    def read_project(cls, r: ByteReader) -> "DbType":
        name = r.read_string()
        fields = [Field(name=r.read_string()) for _ in range(r.read_int())]
        data = [DataRecord(name=r.read_string()) for _ in range(r.read_int())]
        description = r.read_string()
        field_type_list_size = r.read_int()
        if field_type_list_size < len(fields):
            raise WolfFormatError(
                f"type {name!r}: field_type_list_size ({field_type_list_size}) smaller than field "
                f"count ({len(fields)})"
            )
        for f in fields:
            f.type = r.read_byte()
        for _ in range(field_type_list_size - len(fields)):
            r.read_byte()
        for f in fields[: r.read_int()]:
            f.unknown1 = r.read_string()
        for f in fields[: r.read_int()]:
            f.string_args = [r.read_string() for _ in range(r.read_int())]
        for f in fields[: r.read_int()]:
            f.args = [r.read_int() for _ in range(r.read_int())]
        for f in fields[: r.read_int()]:
            f.default_value = r.read_int()
        return cls(name, fields, data, description, field_type_list_size)

    def write_project(self, w: ByteWriter) -> None:
        w.write_string(self.name)
        w.write_int(len(self.fields))
        for f in self.fields:
            w.write_string(f.name)
        w.write_int(len(self.data))
        for d in self.data:
            w.write_string(d.name)
        w.write_string(self.description)
        w.write_int(self.field_type_list_size)
        for f in self.fields:
            w.write_byte(f.type)
        for _ in range(self.field_type_list_size - len(self.fields)):
            w.write_byte(0)
        w.write_int(len(self.fields))
        for f in self.fields:
            w.write_string(f.unknown1)
        w.write_int(len(self.fields))
        for f in self.fields:
            w.write_int(len(f.string_args))
            for s in f.string_args:
                w.write_string(s)
        w.write_int(len(self.fields))
        for f in self.fields:
            w.write_int(len(f.args))
            for a in f.args:
                w.write_int(a)
        w.write_int(len(self.fields))
        for f in self.fields:
            w.write_int(f.default_value)

    def read_dat(self, r: ByteReader) -> None:
        r.verify(_DAT_TYPE_SEPARATOR)
        self.unknown1 = r.read_int()
        fields_size = r.read_int()
        if fields_size != len(self.fields):
            self.fields = self.fields[:fields_size]
        for f in self.fields:
            f.index_info = r.read_int()
        data_size = r.read_int()
        if data_size != len(self.data):
            self.data = self.data[:data_size]
        int_field_count = sum(1 for f in self.fields if not f.is_string())
        string_field_count = sum(1 for f in self.fields if f.is_string())
        for d in self.data:
            d.int_values = [r.read_int() for _ in range(int_field_count)]
            d.string_values = [r.read_string() for _ in range(string_field_count)]

    def write_dat(self, w: ByteWriter) -> None:
        w.write(_DAT_TYPE_SEPARATOR)
        w.write_int(self.unknown1)
        w.write_int(len(self.fields))
        for f in self.fields:
            w.write_int(f.index_info)
        w.write_int(len(self.data))
        for d in self.data:
            for v in d.int_values:
                w.write_int(v)
            for s in d.string_values:
                w.write_string(s)


@dataclass
class WolfDatabase:
    types: list[DbType]
    version: int = _DAT_DEFAULT_VERSION
    is_utf8: bool = False

    @classmethod
    def read(cls, project_path: Path, dat_path: Path) -> "WolfDatabase":
        dat_bytes = dat_path.read_bytes()
        _check_not_encrypted(dat_bytes, dat_path)

        dr = ByteReader(dat_bytes)
        dr.read_byte()  # the "unencrypted" indicator byte, already checked above
        is_utf8 = dr.verify_magic_utf8_aware(_DAT_MAGIC_CP932, _DAT_UTF8_INDEX)
        version = dr.read_byte()
        dat_type_count = dr.read_int()

        # The .project file's own strings share the .dat file's encoding, but
        # that is only knowable after reading the .dat header above -- so the
        # .project file is parsed second, using the now-known encoding.
        encoding = UTF8 if is_utf8 else CP932
        pr = ByteReader(project_path.read_bytes(), encoding=encoding)
        types = [DbType.read_project(pr) for _ in range(pr.read_int())]
        if not pr.eof():
            raise WolfFormatError(f"{project_path}: unexpected trailing data")

        if dat_type_count != len(types):
            raise WolfFormatError(
                f"{dat_path}: type count mismatch (.project has {len(types)}, .dat has {dat_type_count})"
            )
        for t in types:
            t.read_dat(dr)
        terminator = dr.read_byte()
        if terminator != version:
            raise WolfFormatError(
                f"{dat_path}: terminator byte {terminator:#x} does not match version byte {version:#x}"
            )
        if not dr.eof():
            raise WolfFormatError(f"{dat_path}: unexpected trailing data")
        return cls(types, version, is_utf8)

    def write(self, project_path: Path, dat_path: Path) -> None:
        encoding = UTF8 if self.is_utf8 else CP932

        pw = ByteWriter(encoding=encoding)
        pw.write_int(len(self.types))
        for t in self.types:
            t.write_project(pw)
        project_path.write_bytes(pw.getvalue())

        dw = ByteWriter(encoding=encoding)
        dw.write_byte(0)
        magic = bytearray(_DAT_MAGIC_CP932)
        if self.is_utf8:
            magic[_DAT_UTF8_INDEX] = 0x55
        dw.write(bytes(magic))
        dw.write_byte(self.version)
        dw.write_int(len(self.types))
        for t in self.types:
            t.write_dat(dw)
        dw.write_byte(self.version)
        dat_path.write_bytes(dw.getvalue())


def translatable_fields(db_type: DbType) -> list[Field]:
    """String-typed, `type == 0` fields -- mirrors wolftrans's
    Data#each_translatable heuristic (`field.string? && field.type == 0`),
    which the reference tool uses to separate ordinary text fields from
    e.g. dropdown/reference-picker fields that happen to be string-typed."""
    return [f for f in db_type.fields if f.is_string() and f.type == 0]


# ---------------------------------------------------------------------------
# CommonEvent.dat
# ---------------------------------------------------------------------------

_CE_MAGIC_CP932 = bytes([0x57, 0x00, 0x00, 0x4F, 0x4C, 0x00, 0x46, 0x43, 0x00])
_CE_UTF8_INDEX = 5
_CE_DEFAULT_VERSION = 0x8F
_CE_MIN_TERMINATOR = 0x89


@dataclass
class CommonEvent:
    event_id: int
    unknown1: int
    unknown2: bytes  # 7 opaque bytes
    name: str
    commands: list[Command]
    unknown11: str
    description: str
    unknown3: list[str]  # 10 strings, meaning undocumented
    unknown4: list[int]  # 10 bytes
    unknown5: list[list[str]]  # 10 variable-length string arrays
    unknown6: list[list[int]]  # 10 variable-length int arrays
    unknown7: bytes  # 0x1D opaque bytes
    unknown8: list[str]  # 100 strings
    unknown9: str
    unknown10: str | None = None
    unknown12: int | None = None

    @classmethod
    def read(cls, r: ByteReader) -> "CommonEvent":
        indicator = r.read_byte()
        if indicator != 0x8E:
            raise WolfFormatError(f"CommonEvent header indicator not 0x8E (got {indicator:#x})")
        event_id = r.read_int()
        unknown1 = r.read_int()
        unknown2 = r.read(7)
        name = r.read_string()
        commands = [Command.read(r) for _ in range(r.read_int())]
        unknown11 = r.read_string()
        description = r.read_string()
        indicator = r.read_byte()
        if indicator != 0x8F:
            raise WolfFormatError(f"CommonEvent data indicator not 0x8F (got {indicator:#x})")
        unknown3 = [r.read_string() for _ in range(r.read_int())]
        unknown4 = [r.read_byte() for _ in range(r.read_int())]
        unknown5 = [[r.read_string() for _ in range(r.read_int())] for _ in range(r.read_int())]
        unknown6 = [[r.read_int() for _ in range(r.read_int())] for _ in range(r.read_int())]
        unknown7 = r.read(0x1D)
        unknown8 = [r.read_string() for _ in range(100)]
        indicator = r.read_byte()
        if indicator != 0x91:
            raise WolfFormatError(f"CommonEvent indicator not 0x91 (got {indicator:#x})")
        unknown9 = r.read_string()
        unknown10: str | None = None
        unknown12: int | None = None
        indicator = r.read_byte()
        if indicator == 0x92:
            unknown10 = r.read_string()
            unknown12 = r.read_int()
            indicator = r.read_byte()
            if indicator != 0x92:
                raise WolfFormatError(f"CommonEvent trailing indicator not 0x92 (got {indicator:#x})")
        elif indicator != 0x91:
            raise WolfFormatError(f"CommonEvent trailing indicator not 0x91/0x92 (got {indicator:#x})")
        return cls(
            event_id,
            unknown1,
            unknown2,
            name,
            commands,
            unknown11,
            description,
            unknown3,
            unknown4,
            unknown5,
            unknown6,
            unknown7,
            unknown8,
            unknown9,
            unknown10,
            unknown12,
        )

    def write(self, w: ByteWriter) -> None:
        w.write_byte(0x8E)
        w.write_int(self.event_id)
        w.write_int(self.unknown1)
        w.write(self.unknown2)
        w.write_string(self.name)
        w.write_int(len(self.commands))
        for c in self.commands:
            c.write(w)
        w.write_string(self.unknown11)
        w.write_string(self.description)
        w.write_byte(0x8F)
        w.write_int(len(self.unknown3))
        for s in self.unknown3:
            w.write_string(s)
        w.write_int(len(self.unknown4))
        for b in self.unknown4:
            w.write_byte(b)
        w.write_int(len(self.unknown5))
        for group in self.unknown5:
            w.write_int(len(group))
            for s in group:
                w.write_string(s)
        w.write_int(len(self.unknown6))
        for group in self.unknown6:
            w.write_int(len(group))
            for v in group:
                w.write_int(v)
        w.write(self.unknown7)
        for s in self.unknown8:
            w.write_string(s)
        w.write_byte(0x91)
        w.write_string(self.unknown9)
        if self.unknown10 is not None:
            w.write_byte(0x92)
            w.write_string(self.unknown10)
            w.write_int(self.unknown12 or 0)
            w.write_byte(0x92)
        else:
            w.write_byte(0x91)


@dataclass
class WolfCommonEvents:
    events: list[CommonEvent]
    version: int = _CE_DEFAULT_VERSION
    terminator: int = _CE_DEFAULT_VERSION
    is_utf8: bool = False

    @classmethod
    def read(cls, path: Path) -> "WolfCommonEvents":
        data = path.read_bytes()
        _check_not_encrypted(data, path)
        r = ByteReader(data)
        r.read_byte()  # the "unencrypted" indicator byte, already checked above
        is_utf8 = r.verify_magic_utf8_aware(_CE_MAGIC_CP932, _CE_UTF8_INDEX)
        version = r.read_byte()
        events = [CommonEvent.read(r) for _ in range(r.read_int())]
        terminator = r.read_byte()
        if terminator < _CE_MIN_TERMINATOR:
            raise WolfFormatError(f"{path}: terminator {terminator:#x} smaller than {_CE_MIN_TERMINATOR:#x}")
        if not r.eof():
            raise WolfFormatError(f"{path}: unexpected trailing data")
        return cls(events, version, terminator, is_utf8)

    def write(self, path: Path) -> None:
        encoding = UTF8 if self.is_utf8 else CP932
        w = ByteWriter(encoding=encoding)
        w.write_byte(0)
        magic = bytearray(_CE_MAGIC_CP932)
        if self.is_utf8:
            magic[_CE_UTF8_INDEX] = 0x55
        w.write(bytes(magic))
        w.write_byte(self.version)
        w.write_int(len(self.events))
        for e in self.events:
            e.write(w)
        w.write_byte(self.terminator)
        path.write_bytes(w.getvalue())


# ---------------------------------------------------------------------------
# Generic locator walker (attribute-name / list-index path), used by wolf.py
# to get/set a TextUnit's translated text back into the parsed dataclasses
# above -- same spirit as _rgss_common.py's locator_get/locator_set for
# RubyObject attributes, adapted for plain dataclasses via getattr/setattr.
# ---------------------------------------------------------------------------


def locator_get(root: object, locator: str) -> object:
    cur = root
    for seg in locator.split("/"):
        cur = cur[int(seg)] if seg.lstrip("-").isdigit() else getattr(cur, seg)  # type: ignore[index]
    return cur


def locator_set(root: object, locator: str, value: object) -> None:
    segments = locator.split("/")
    cur = root
    for seg in segments[:-1]:
        cur = cur[int(seg)] if seg.lstrip("-").isdigit() else getattr(cur, seg)  # type: ignore[index]
    last = segments[-1]
    if last.lstrip("-").isdigit():
        cur[int(last)] = value  # type: ignore[index]
    else:
        setattr(cur, last, value)


def iter_command_texts(commands: list[Command], locator_prefix: str) -> Iterator[tuple[str, str]]:
    """Yields (locator, text) for every translatable string slot in a
    command list, where locator is relative to whatever object `commands`
    lives on (caller prefixes it with the path down to that list)."""
    for ci, cmd in enumerate(commands):
        for slot in command_text_slots(cmd):
            yield f"{locator_prefix}/{ci}/string_args/{slot}", cmd.string_args[slot]
