from __future__ import annotations

from pathlib import Path

from rubymarshal.classes import RubyObject

from rpg_translator.codec.rvdata2_codec import read_rvdata2, write_rvdata2


def test_write_rvdata2_preserves_shared_object_identity_after_plain_string_field(tmp_path: Path):
    """M4.9 用真实 RPG Maker VX 工程（GitHub 上的开源同人游戏 flower-in-pain）
    实测发现的 `rubymarshal` 库 bug：Marshal 的对象反向引用（TYPE_LINK）靠一份
    "写到第几个对象了"的计数表对齐，读的时候每遇到一个字符串（不管有没有被
    编码标记包一层）都会占一个编号，但 `rubymarshal.writer.Writer` 写普通
    `str`/`bytes` 时不会占这个编号（只有 `RubyString` 会）——`_rgss_common.py`
    每次回填文本都会把读出来的 `RubyString` 换成纯 `str`/`bytes`（不管是不是
    翻译过），一转换就把后面所有对象的编号带偏。真实数据上这会导致回填后的
    文件读不回来（`ValueError: invalid link destination`），或者更隐蔽地——像
    这个用例复现的——静默读出错误对象（原本应该是共享的空列表，读出来变成
    了完全无关的另一个对象），不报错但数据是坏的，比直接崩溃更危险。这条
    测试固定住 `rvdata2_codec._SafeWriter` 的修复：字符串字段后面如果跟着一个
    真的被多处共享（Python 对象identity相同）的子对象，回填后要么原样保留
    共享关系，不能变成两个不相关的独立对象。
    """
    shared_route: list = []
    root = RubyObject(
        "Test::Root",
        {
            "@text": "hello",  # 模拟 locator_set 回填文本时换上的纯 str
            "@a": RubyObject("Test::Child", {"@route": shared_route}),
            "@b": RubyObject("Test::Child", {"@route": shared_route}),
        },
    )

    path = tmp_path / "shared.dat"
    write_rvdata2(path, root)
    reloaded = read_rvdata2(path)

    assert reloaded.attributes["@text"] == "hello"
    route_a = reloaded.attributes["@a"].attributes["@route"]
    route_b = reloaded.attributes["@b"].attributes["@route"]
    assert route_a == []
    assert route_b == [], f"expected an empty list, got corrupted object: {route_b!r}"
    assert route_a is route_b, "共享引用关系必须保留，不能变成两个独立对象"


def test_write_rvdata2_round_trips_bytes_valued_field_before_shared_object(tmp_path: Path):
    """同一个 bug 在 XP/VX 老版本 Ruby 的 `bytes` 字符串（不经过 RubyString/
    ivar 包装）上的对应场景。"""
    shared_route: list = []
    root = RubyObject(
        "Test::Root",
        {
            "@text": b"hello",  # XP/VX 老版本 Ruby 字符串的原生格式
            "@a": RubyObject("Test::Child", {"@route": shared_route}),
            "@b": RubyObject("Test::Child", {"@route": shared_route}),
        },
    )

    path = tmp_path / "shared_bytes.dat"
    write_rvdata2(path, root)
    reloaded = read_rvdata2(path)

    assert reloaded.attributes["@text"] == b"hello"
    route_a = reloaded.attributes["@a"].attributes["@route"]
    route_b = reloaded.attributes["@b"].attributes["@route"]
    assert route_a == [] and route_b == []
    assert route_a is route_b


def test_write_rvdata2_many_strings_do_not_exhaust_or_corrupt_gc_reused_ids(tmp_path: Path):
    """`must_write` 靠 `id(obj)` 判断对象是否写过，只存编号没保留引用会被
    CPython 内存地址复用坑到（见 _SafeWriter 的 keep_alive）——写一长串会被
    立刻当场垃圾回收的临时字符串/列表，确认不会因为地址复用被误判成
    "之前写过同一个对象"。"""
    shared_marker = ["marker"]
    root = RubyObject(
        "Test::Root",
        {
            "@lines": [str(i) for i in range(500)],
            "@first": RubyObject("Test::Child", {"@marker": shared_marker}),
            "@second": RubyObject("Test::Child", {"@marker": shared_marker}),
        },
    )

    path = tmp_path / "many.dat"
    write_rvdata2(path, root)
    reloaded = read_rvdata2(path)

    assert reloaded.attributes["@lines"] == [str(i) for i in range(500)]
    assert reloaded.attributes["@first"].attributes["@marker"] == ["marker"]
    assert (
        reloaded.attributes["@first"].attributes["@marker"]
        is reloaded.attributes["@second"].attributes["@marker"]
    )
