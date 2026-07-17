from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer

from rpg_translator.config import Settings, get_deepseek_api_key
from rpg_translator.core.pipeline import (
    MissingApiKeyError,
    UnknownEngineError,
    run_extract,
    run_full,
    run_glossary,
    run_inject,
    run_translate,
)

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

app = typer.Typer(help="RPG Maker MV/MZ/VX Ace 文本提取/翻译/回填工具（开发调试用 CLI）")

_NOT_IMPLEMENTED_MILESTONE = {
    "qa": "M3",
}


def _not_implemented(command: str) -> None:
    milestone = _NOT_IMPLEMENTED_MILESTONE[command]
    typer.echo(f"`{command}` 尚未实现，计划在里程碑 {milestone} 完成。", err=True)
    raise typer.Exit(code=1)


@app.command()
def extract(
    project_dir: Annotated[Path, typer.Argument(help="游戏工程根目录")],
    out: Annotated[Path, typer.Option(help="输出的 SQLite 数据库路径")] = Path("units.db"),
) -> None:
    """从游戏工程提取文本到 SQLite 数据库。"""
    try:
        units = run_extract(project_dir, out)
    except UnknownEngineError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"提取完成：{len(units)} 条文本，已写入 {out}")


@app.command()
def glossary(
    db: Annotated[Path, typer.Option(help="SQLite 数据库路径")] = Path("units.db"),
) -> None:
    """抽取术语表候选并存入数据库。"""
    settings = Settings()
    try:
        candidates = asyncio.run(
            run_glossary(db, get_deepseek_api_key(), settings.deepseek_base_url, settings.deepseek_model)
        )
    except MissingApiKeyError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"术语抽取完成：{len(candidates)} 条候选，已写入 {db}")


@app.command()
def translate(
    db: Annotated[Path, typer.Option(help="SQLite 数据库路径")] = Path("units.db"),
    concurrency: Annotated[int, typer.Option(help="并发请求数")] = 8,
) -> None:
    """调用 DeepSeek 批量翻译数据库中待翻译的 TextUnit。"""
    settings = Settings()
    try:
        translated = asyncio.run(
            run_translate(
                db,
                get_deepseek_api_key(),
                settings.deepseek_base_url,
                settings.deepseek_model,
                concurrency,
            )
        )
    except MissingApiKeyError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"翻译完成：数据库中共有 {len(translated)} 条已翻译文本")


@app.command()
def qa(
    db: Annotated[Path, typer.Option(help="SQLite 数据库路径")] = Path("units.db"),
    export: Annotated[Path | None, typer.Option(help="导出待复核列表 CSV 路径")] = None,
) -> None:
    """一致性校验：标记同一原文在不同语境下的疑似冲突。"""
    _not_implemented("qa")


@app.command()
def inject(
    db: Annotated[Path, typer.Option(help="SQLite 数据库路径")] = Path("units.db"),
    project: Annotated[Path, typer.Option(help="原始游戏工程根目录")] = Path("."),
    out: Annotated[Path, typer.Option(help="汉化版输出目录")] = Path("output"),
) -> None:
    """把翻译结果写回到新的输出目录。"""
    try:
        units = run_inject(project, db, out)
    except UnknownEngineError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"写回完成：{len(units)} 条文本，输出到 {out}")


@app.command()
def run(
    project_dir: Annotated[Path, typer.Argument(help="游戏工程根目录")],
    out: Annotated[Path, typer.Option(help="汉化版输出目录")] = Path("output"),
    db: Annotated[Path, typer.Option(help="SQLite 数据库路径")] = Path("units.db"),
    concurrency: Annotated[int, typer.Option(help="并发请求数")] = 8,
) -> None:
    """一键跑完整链路：extract -> 术语抽取 -> translate -> inject。"""
    settings = Settings()
    try:
        units = asyncio.run(
            run_full(
                project_dir,
                db,
                out,
                get_deepseek_api_key(),
                settings.deepseek_base_url,
                settings.deepseek_model,
                concurrency,
            )
        )
    except (UnknownEngineError, MissingApiKeyError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from e
    typer.echo(f"完整汉化流程完成：{len(units)} 条文本，输出到 {out}")


if __name__ == "__main__":
    app()
