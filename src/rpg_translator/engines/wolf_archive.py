"""解包 WOLF RPG Editor 打包发行版的 Data.wolf（DXArchive 容器）。

背景：`engines/wolf.py`/`wolf_binary.py` 解析的是已经解压好的 `Data/BasicData/*.dat`、
`Data/MapData/*.mps` 这些明文文件。但很多发行版游戏会把整个 `Data/` 目录再打包成一个
`Data.wolf`（DxLib 的 DXArchive 容器格式，魔数 "DX"）放在游戏根目录，`wolf.py` 的
`detect()` 找不到解压后的目录结构，直接判定"不支持的引擎"。

这个模块不去重新实现 DXArchive 的解密——实测（针对一份真实游戏的 Data.wolf）发现
它除了标准 DXArchive 格式外，还叠加了一层目前没有任何公开资料（wolftrans、
rewolf-trans、WolfTL、WolfDec 的源码及其内置密钥列表）记录过的额外混淆：文件头里
定位实际数据的几个地址字段，用这些工具已知的全部固定密钥都解不开，说明用的
WOLF RPG Editor/DxLib 版本比这些社区工具最后一次更新时更新。继续纯 Python 逆向
这层未知混淆算法，正确性没有把握、时间成本也不可控。

改为调用 UberWolfCli.exe（Sinflower/UberWolf，MIT License，社区里持续维护、能自动
识别游戏用的具体密钥版本）做解包这一步的后端——已经针对真实游戏文件验证过能正确
解包（见 resources/wolf_dec/SOURCES.md），解包出来的明文 Data/ 目录直接交给本项目
自己的 wolf.py/wolf_binary.py（这部分已验证能正确解析）处理。

解包成功后会把原始 Data.wolf 挪到 .rpg_translator_backup/ 下而不是留在原地——WOLF
RPG Editor 运行时的加载顺序是"目录下同名明文文件优先于 .wolf 包内文件"（vgperson
的 WOLF 汉化教程明确写了这条，社区共识）；对于"整个 Data 目录打包成单个 Data.wolf"
这种布局，教程原话是必须删除/改名这个 .wolf 文件，否则游戏运行时仍然会优先从包内
读取（未翻译的）原文，明文 Data/ 目录里即使已经是译文也不会生效。挪到备份目录而不
直接删，是为了可以随时恢复成原版（未打包也未翻译）状态。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from rpg_translator.translate.local_engine import get_app_root

logger = logging.getLogger(__name__)

_UBERWOLF_CLI_RELATIVE_PATH = Path("resources") / "wolf_dec" / "UberWolfCli.exe"
_BACKUP_DIR_NAME = ".rpg_translator_backup"
_UNPACK_TIMEOUT_SECONDS = 600.0

# WOLF RPG Editor 游戏根目录下常见的非游戏本体 exe——找"游戏本体 exe"时要排除，
# 不然可能误把这些小工具当成游戏本体传给 UberWolfCli。
_KNOWN_NON_GAME_EXE_NAMES = {
    "config.exe",
    "unins000.exe",
    "uninstall.exe",
    "unitycrashhandler32.exe",
    "unitycrashhandler64.exe",
}


class WolfArchiveError(Exception):
    pass


def find_uberwolf_cli(app_root: Path | None = None) -> Path | None:
    root = app_root if app_root is not None else get_app_root()
    exe_path = root / _UBERWOLF_CLI_RELATIVE_PATH
    return exe_path if exe_path.is_file() else None


def _find_data_wolf(project_dir: Path) -> Path | None:
    for child in project_dir.iterdir():
        if child.is_file() and child.name.lower() == "data.wolf":
            return child
    return None


def is_packed_wolf_project(project_dir: Path) -> bool:
    """project_dir 是不是"整个 Data 目录打包成单个 Data.wolf"这种发行版布局、
    且还没解包过。已经解包过（Data/BasicData 已存在）就不再算，避免每次调用
    detect_adapter() 都重复触发一次解包尝试。"""
    if (project_dir / "Data" / "BasicData").is_dir():
        return False
    return _find_data_wolf(project_dir) is not None


def _find_game_exe(project_dir: Path) -> Path | None:
    """UberWolfCli 需要指向游戏本体 exe 才能可靠工作——实测直接把 Data.wolf 路径
    传给它，它内部"从归档反查游戏 exe"的兜底逻辑找不到文件，直接失败（见调用方
    ensure_wolf_unpacked 的说明）。同目录下排除掉已知的工具类 exe 后，游戏本体 exe
    通常就剩一个；如果还剩不止一个，选体积最大的那个——游戏本体内嵌了运行时资源，
    明显比 Config.exe 这类工具 exe 大。"""
    candidates = [
        p
        for p in project_dir.glob("*.exe")
        if p.name.lower() not in _KNOWN_NON_GAME_EXE_NAMES
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def ensure_wolf_unpacked(project_dir: Path) -> bool:
    """如果 project_dir 是"打包发行版"布局，解包 Data.wolf 到明文 Data/ 目录，
    并把原始 Data.wolf 备份挪走（见模块说明）。返回是否真的执行了解包；不是这种
    布局、或已经解包过时返回 False，直接跳过——可以放心在每次 detect_adapter() 时
    都调用一遍。"""
    if not is_packed_wolf_project(project_dir):
        return False

    wolf_file = _find_data_wolf(project_dir)
    assert wolf_file is not None  # is_packed_wolf_project 已经确认过

    exe_path = find_uberwolf_cli()
    if exe_path is None:
        raise WolfArchiveError(
            f"检测到打包发行版的 {wolf_file.name}，但本机没有找到解包工具 "
            "UberWolfCli.exe（resources/wolf_dec/）。请先跑一次 "
            "`.venv\\Scripts\\python.exe scripts\\fetch_wolf_dec.py` 下载。"
        )

    game_exe = _find_game_exe(project_dir)
    if game_exe is None:
        raise WolfArchiveError(
            f"检测到打包发行版的 {wolf_file.name}，但在 {project_dir} 下没找到"
            "游戏本体 exe，无法解包（UberWolfCli 需要指向游戏本体 exe 才能正常工作）。"
        )

    logger.info("检测到打包发行版 WOLF 工程，正在用 UberWolfCli 解包 %s ...", wolf_file.name)
    try:
        result = subprocess.run(
            [str(exe_path), "-o", str(game_exe)],
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_UNPACK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise WolfArchiveError(f"UberWolfCli 解包 {wolf_file.name} 超时") from exc

    if result.returncode != 0 or not (project_dir / "Data" / "BasicData").is_dir():
        raise WolfArchiveError(
            f"UberWolfCli 解包 {wolf_file.name} 失败（exit={result.returncode}）：\n"
            f"{result.stdout}\n{result.stderr}"
        )

    backup_dir = project_dir / _BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / wolf_file.name
    if backup_path.exists():
        wolf_file.unlink()  # 备份已经存在（比如重跑过一次），原文件直接丢弃不留冗余
    else:
        shutil.move(str(wolf_file), str(backup_path))

    logger.info("解包完成，原始 %s 已备份到 %s", wolf_file.name, backup_path)
    return True
