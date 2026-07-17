from typer.testing import CliRunner

from rpg_translator.cli import app

runner = CliRunner()


def test_help_lists_all_subcommands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ["extract", "glossary", "translate", "qa", "inject", "run"]:
        assert command in result.output


def test_extract_stub_exits_nonzero_with_milestone_message(tmp_path):
    result = runner.invoke(app, ["extract", str(tmp_path)])
    assert result.exit_code == 1
    assert "M1" in result.output


def test_translate_stub_exits_nonzero_with_milestone_message():
    result = runner.invoke(app, ["translate"])
    assert result.exit_code == 1
    assert "M2" in result.output
