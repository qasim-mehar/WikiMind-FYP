from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from pypdf import PdfWriter
from typer.testing import CliRunner

from wikimind.parse import cli, parse_file
from wikimind.parse.cli import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOTE = FIXTURES / "papers" / "contradiction-note.txt"


def test_run_json_is_canonical() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run", str(NOTE), "--json"])
    assert result.exit_code == 0, result.output
    assert result.output.rstrip("\n") == parse_file(NOTE).model_dump_json()

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


def test_run_grobid_invalid_url_is_a_clean_error(monkeypatch) -> None:
    monkeypatch.setenv("WIKIMIND_GROBID_URL", "http://[::1")
    runner = CliRunner()
    result = runner.invoke(app, ["run", str(NOTE), "--parser", "grobid"])
    assert result.exit_code == 1
    assert "parse error" in result.output
    assert "not a valid URL" in result.output


def test_run_encrypted_pdf_is_a_clean_error(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    path = tmp_path / "locked.pdf"
    path.write_bytes(buffer.getvalue())

    runner = CliRunner()
    result = runner.invoke(app, ["run", str(path)])
    assert result.exit_code == 1
    assert "parse error" in result.output
    assert "encrypted PDF" in result.output


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
