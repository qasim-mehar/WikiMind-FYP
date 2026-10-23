from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from wikimind.parse import cli
from wikimind.parse.cli import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOTE = FIXTURES / "papers" / "contradiction-note.txt"


def test_run_json() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", str(NOTE), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["parser"] == "text"
    assert payload["source_filename"] == "contradiction-note.txt"
    assert len(payload["channels"]) == 1
    assert payload["channels"][0]["kind"] == "section"


def test_run_summary_and_explicit_parser() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", str(NOTE), "--parser", "text"])
    assert result.exit_code == 0, result.output
    assert "parser: text" in result.output
    assert "channels: 1" in result.output
    assert "section: 1" in result.output


def test_run_unknown_parser_fails_cleanly() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", str(NOTE), "--parser", "nope"])
    assert result.exit_code == 1
    assert "parse error" in result.output


def test_grobid_check_without_environment(
    monkeypatch,
) -> None:
    monkeypatch.delenv("WIKIMIND_GROBID_URL", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["grobid-check"])
    assert result.exit_code == 1
    assert "WIKIMIND_GROBID_URL" in result.output


def test_grobid_check_success(monkeypatch) -> None:
    monkeypatch.setattr(cli, "probe_grobid", lambda: (True, "GROBID is reachable"))
    runner = CliRunner()
    result = runner.invoke(app, ["grobid-check"])
    assert result.exit_code == 0
    assert "reachable" in result.output


def test_module_help_is_runnable_standalone() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "wikimind.parse.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "run" in result.stdout
    assert "grobid-check" in result.stdout
