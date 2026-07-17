from pathlib import Path

from typer.testing import CliRunner

from rpg_translator.cli import app

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


def test_translate_stub_exits_nonzero_with_milestone_message():
    result = runner.invoke(app, ["translate"])
    assert result.exit_code == 1
    assert "M2" in result.output
