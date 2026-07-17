from __future__ import annotations

from pathlib import Path
from typing import Any

from rubymarshal.reader import load
from rubymarshal.writer import Writer, writes

# M4.9 用真实 RPG Maker VX 工程（GitHub 上的开源同人游戏 flower-in-pain）实测
# 发现：inject() 回填后，8 个真实地图文件里有 3 个连自己都读不回来
# （`ValueError: invalid link destination`）——意味着真实游戏引擎大概率也读不了，
# 直接把地图读崩。根因是 `rubymarshal.writer.Writer` 里两个配套的 bug：
#
# 1. Marshal 格式的对象反向引用（TYPE_LINK）靠一份"写到第几个对象了"的计数表
#    对齐——读的时候每遇到一个 TYPE_STRING（不管有没有被 TYPE_IVAR 包一层编码
#    标记）都会往这份计数表里占一个编号；但写的时候，`write_string`（用于
#    普通 Python `str`）和 `write_bytes`（用于 `bytes`，XP/VX 老版本 Ruby 字符串
#    的原生格式）都不会调用负责占编号的 `must_write`——只有 `write_ruby_string`
#    （用于 `RubyString`）会。本仓库的 `_rgss_common.py` 每次回填文本都会把
#    读出来的 `RubyString` 换成纯 `str`/`bytes`（不管是不是翻译过，未翻译也会
#    走一遍这个转换），一转换就绕开了 `must_write`，后面所有对象的编号从这里
#    开始跟读的时候对不上——只要这条文本后面恰好跟着一个真的被反向引用过的
#    共享对象（真实地图很常见，比如多个事件页共用同一个空 `@move_route`
#    模板），写出来的链接编号就是错的。
# 2. `must_write` 本身用 `id(obj)` 当对象是否已经写过的唯一依据，但只存了
#    编号没保留对象引用——写入过程中一旦某个临时对象被垃圾回收，CPython 会
#    把同一块内存地址立刻复用给另一个无关对象，被误判成"之前写过"。
#
# 下面这个子类把这两处都堵上：在最外层的 `write` 分发处拦截 str/bytes，补上
# 跟 `write_ruby_string` 一样的 `must_write` 登记步骤（不能直接改
# `write_string`/`write_bytes` 本身——它们内部也会递归调用同名方法写字符串
# 内容的字节数据，那层不该重复登记，只有顶层值才该登记一次）；`must_write`
# 本身额外存一份强引用防止对象被提前回收。


class _SafeWriter(Writer):
    def __init__(self, fd: Any) -> None:
        super().__init__(fd)
        self._keep_alive: list[Any] = []

    def must_write(self, obj: Any) -> bool:
        is_new = super().must_write(obj)
        if is_new:
            self._keep_alive.append(obj)
        return is_new

    def write(self, obj: Any) -> None:
        # 只拦 str/bytes 这两个顶层值类型补登记；其余类型（含它们内部会
        # 递归调用到的 write_bytes 之类的底层原语）原样走父类逻辑，不然会
        # 重复登记、把编号又搞错方向。
        if isinstance(obj, bytes):
            if self.must_write(obj):
                super().write_bytes(obj)
        elif isinstance(obj, str):
            if self.must_write(obj):
                super().write_string(obj)
        else:
            super().write(obj)


def read_rvdata2(path: Path) -> Any:
    with open(path, "rb") as f:
        return load(f)


def write_rvdata2(path: Path, obj: Any) -> None:
    path.write_bytes(writes(obj, cls=_SafeWriter))
