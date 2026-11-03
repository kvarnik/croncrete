"""CLI exposes the five thin wrappers."""

from typer.testing import CliRunner

from croncrete.cli import app

runner = CliRunner()


def test_help_lists_five_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("compile", "run", "repair", "resume", "report"):
        assert cmd in result.output
