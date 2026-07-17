from pathlib import Path

import pytest
from typer.testing import CliRunner

from rpg_translator.cli import app
from rpg_translator.config import get_deepseek_api_key

runner = CliRunner()


def test_help_lists_all_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ["extract", "glossary", "translate", "qa", "inject", "run"]:
        assert command in result.output


def test_extract_unknown_engine_exits_nonzero_with_clear_message(tmp_path):
    result = runner.invoke(app, ["extract", str(tmp_path)])
    assert result.exit_code == 1
    assert "未识别到支持的 RPG Maker 引擎" in result.output


def test_extract_then_inject_roundtrip_via_cli(tmp_path, mz_project: Path):
    db_path = tmp_path / "units.db"
    output_dir = tmp_path / "output"

    extract_result = runner.invoke(app, ["extract", str(mz_project), "--out", str(db_path)])
    assert extract_result.exit_code == 0
    assert "提取完成" in extract_result.output
    assert db_path.is_file()

    inject_result = runner.invoke(
        app,
        ["inject", "--db", str(db_path), "--project", str(mz_project), "--out", str(output_dir)],
    )
    assert inject_result.exit_code == 0
    assert "写回完成" in inject_result.output
    assert (output_dir / "data" / "System.json").is_file()


def test_translate_without_api_key_exits_nonzero_with_clear_message(tmp_path, monkeypatch):
    monkeypatch.setattr("rpg_translator.cli.get_deepseek_api_key", lambda: None)
    db_path = tmp_path / "units.db"
    result = runner.invoke(app, ["translate", "--db", str(db_path)])
    assert result.exit_code == 1
    assert "未配置 DeepSeek API Key" in result.output


def test_glossary_and_translate_full_cli_flow_against_real_provider(tmp_path, mz_project: Path):
    if not get_deepseek_api_key():
        pytest.skip("本地未配置 DEEPSEEK_API_KEY，跳过真实 API 调用测试")

    db_path = tmp_path / "units.db"
    output_dir = tmp_path / "output"

    extract_result = runner.invoke(app, ["extract", str(mz_project), "--out", str(db_path)])
    assert extract_result.exit_code == 0

    glossary_result = runner.invoke(app, ["glossary", "--db", str(db_path)])
    assert glossary_result.exit_code == 0
    assert "术语抽取完成" in glossary_result.output

    translate_result = runner.invoke(
        app, ["translate", "--db", str(db_path), "--concurrency", "4"]
    )
    assert translate_result.exit_code == 0
    assert "翻译完成" in translate_result.output

    inject_result = runner.invoke(
        app,
        ["inject", "--db", str(db_path), "--project", str(mz_project), "--out", str(output_dir)],
    )
    assert inject_result.exit_code == 0

    import json

    translated_map001 = json.loads((output_dir / "data" / "Map001.json").read_text(encoding="utf-8"))
    line1 = translated_map001["events"][1]["pages"][0]["list"][1]["parameters"][0]
    assert line1 != "こんにちは、旅人よ。"  # 真的被翻译过，不是原文本
    assert "⟦CC" not in line1
