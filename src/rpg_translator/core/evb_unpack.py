from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

from evbunpack.const import EVB_MAGIC
from evbunpack.__main__ import main as _evbunpack_main, search_for_magic

# EVB 的虚拟文件系统表紧跟在原始 PE 数据后面，不在文件开头——但也不会离开头太远
# （原始 PE 本体 + 两个 .enigma 节），拖进来的可能是几个 GB 的游戏本体，没必要为了
# 找一个 magic 扫完整个文件；这个窗口对目前见过的 RPG Maker MV/MZ 单文件游戏够用。
_MAGIC_SEARCH_WINDOW = 256 * 1024 * 1024

# contextlib.redirect_stdout/redirect_stderr 改的是进程全局的 sys.stdout/sys.stderr，
# Python 官方文档明确标注这两个上下文管理器不是线程安全的——unpack_evb() 在
# gui/workers.py 的 UnpackWorker 这个独立 QThread 里跑，这把锁至少保证这个项目自己
# 不会同时跑两个 unpack_evb()（互相用同一份全局 stdout/stderr 抢着换入换出）；管不住
# 的是解包期间恰好有别的线程直接往 sys.stdout/sys.stderr 写字符（比如某个三方依赖
# 库自己 print/warnings.warn），那部分输出理论上仍可能被这段重定向影响到——目前代码
# 路径下没有别的线程会在这个窗口内这么做，这把锁主要是防住"以后允许并发解包"这种
# 改动引入的自我竞争。
_unpack_lock = threading.Lock()


def is_evb_packed(exe_path: Path) -> bool:
    """判断这个 exe 是不是用 Enigma Virtual Box 打包的单文件游戏——资源（RPG Maker
    MV/MZ 的话就是 www/data 等目录）和 nw.js 运行时全部封进了这一个 exe，磁盘上
    没有散落的工程文件，现有的按目录扫文件判断引擎的 detect() 天然找不到东西。
    这里只找 magic 头，不做完整解析，用于"要不要尝试解包"的轻量判断。"""
    if not exe_path.is_file():
        return False
    size = exe_path.stat().st_size
    if size == 0:
        return False
    with open(exe_path, "rb") as fd:
        window = min(size, _MAGIC_SEARCH_WINDOW)
        return search_for_magic(fd, window, EVB_MAGIC) >= 0


def find_evb_candidate(dropped_dir: Path) -> Path | None:
    """在拖进来的目录顶层找一个 EVB 打包的 exe——只找顶层，EVB 打包的单文件游戏
    本体就是拖入目录下的那一个 exe，不用递归翻子目录（也避免游戏自带的
    卸载程序/其它工具 exe 干扰判断，虽然那些一般也不会是 EVB 打包）。"""
    if not dropped_dir.is_dir():
        return None
    for candidate in sorted(dropped_dir.glob("*.exe")):
        if is_evb_packed(candidate):
            return candidate
    return None


def unpack_evb(exe_path: Path, out_dir: Path) -> None:
    """把 exe_path 解包到 out_dir：既还原虚拟文件系统（游戏本体的 www/data 等目录，
    RPGTranslator 后续按普通工程目录识别/提取），也还原一份能跑的 exe（不然解包
    出来的只有文本，用户没法直接验证或游玩）。

    evbunpack 内部把逐 chunk 的解压/写入进度直接 sys.stderr.write（大文件能刷出
    几万行），这些进度对我们没有意义——和 gui/main_window.py 那次"跨线程高频
    appendPlainText 撑爆崩溃"是同一类坑，这里整段调用期间把 stdout/stderr 换成
    黑洞；真正有意义的阶段性消息（"正在解包""解包完成"这几条）走的是 Python
    logging 模块、不受这个重定向影响，仍然会落进 logging_setup.py 配的文件日志。

    evbunpack 的 main() 对内部各阶段的异常是自己 log 一下就吞掉、不向外抛的（见
    evbunpack.__main__.main 源码），所以这里不能靠"有没有抛异常"判断解包是否真的
    有用——调用方应该在这个函数返回后，用现有的 detect_adapter() 重新探测 out_dir，
    探测成功才算数。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with _unpack_lock:
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                _evbunpack_main(str(exe_path), str(out_dir))
