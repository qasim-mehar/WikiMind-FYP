"""Lane 16 CLI tests for ``wikimind add``.

No live provider is ever used: the confirmation and flow paths are wired with
a stubbed ``add_documents``; the extraction-only path runs against the sandbox
throwaway database created by conftest.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from wikimind.cli import app as root_app
from wikimind.compile.graph import create_project
from wikimind.config import Settings
from wikimind.ingest import IngestOutcome, estimate_compile_cost
from wikimind.ingest import cli as ingest_cli

runner = CliRunner(env={"COLUMNS": "200"})


def test_add_help() -> None:
    result = runner.invoke(root_app, ["add", "--help"])
    assert result.exit_code == 0
    # Rich styling can insert ANSI codes *inside* an option name, so strip them
    # before checking (same convention as test_compile_cli.plain_help).
    flat = re.sub(r"\s+", "", re.sub(r"\x1b\[[0-9;]*m", "", result.output))
    for option in ("--force", "--yes", "--no-compile", "--claims-per-unit", "--create-project"):
        assert option in flat


def test_add_no_dsn_reports_friendly_error(monkeypatch, tmp_path) -> None:
    # Stub settings instead of mutating process env: CI may inject WIKIMIND_DB
    # at the job level, and .env loading must not affect this assertion.
    monkeypatch.setattr(ingest_cli, "get_settings", lambda: Settings())
    source = tmp_path / "note.txt"
    source.write_text("text", encoding="utf-8")

    result = runner.invoke(root_app, ["add", "proj", str(source)])

    assert result.exit_code == 2
    assert "no database DSN" in result.output
    assert "hint:" in result.output


def test_add_no_compile_cli(test_dsn: str, tmp_path: Path) -> None:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(test_dsn, row_factory=dict_row, autocommit=True) as conn:
        project = create_project(conn, f"lane16-cli-{uuid4().hex[:12]}").name
    source = tmp_path / "note.txt"
    source.write_text("Alpha joined Beta Labs in 2017.", encoding="utf-8")

    first = runner.invoke(
        root_app, ["add", project, str(source), "--no-compile", "--dsn", test_dsn]
    )
    assert first.exit_code == 0, first.output
    assert "[1/3] extract" in first.output
    assert "registered: note.txt" in first.output
    assert "skipped: compile disabled (--no-compile)" in first.output

    second = runner.invoke(
        root_app, ["add", project, str(source), "--no-compile", "--dsn", test_dsn]
    )
    assert second.exit_code == 0, second.output
    assert "already registered: note.txt" in second.output


def test_add_yes_skips_confirmation(test_dsn: str, tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_add(conn, **kwargs):
        captured.update(kwargs)
        estimate = estimate_compile_cost(["passage"])
        assert kwargs["confirm"](estimate) is True
        return IngestOutcome(project_name="stub-project", extracted=[])

    monkeypatch.setattr(ingest_cli, "add_documents", fake_add)
    source = tmp_path / "note.txt"
    source.write_text("text", encoding="utf-8")

    result = runner.invoke(root_app, ["add", "proj", str(source), "--dsn", test_dsn, "--yes"])

    assert result.exit_code == 0, result.output
    assert "done: project stub-project" in result.output
    assert captured["confirm"](estimate_compile_cost(["passage"])) is True


def test_add_default_prompt_can_be_declined(test_dsn: str, tmp_path: Path, monkeypatch) -> None:
    prompts: list[str] = []

    def fake_confirm(text: str, **_kwargs) -> bool:
        prompts.append(text)
        return False

    def fake_add(conn, **kwargs):
        estimate = estimate_compile_cost(["passage"])
        assert kwargs["confirm"](estimate) is False
        return IngestOutcome(
            project_name="stub-project",
            extracted=[],
            skipped_reason="declined by user",
        )

    monkeypatch.setattr(ingest_cli.typer, "confirm", fake_confirm)
    monkeypatch.setattr(ingest_cli, "add_documents", fake_add)
    source = tmp_path / "note.txt"
    source.write_text("text", encoding="utf-8")

    result = runner.invoke(root_app, ["add", "proj", str(source), "--dsn", test_dsn])

    assert result.exit_code == 0, result.output
    assert prompts and "Proceed with compile" in prompts[0]
    assert "skipped: declined by user" in result.output


def test_add_exit_code_for_ingest_error(test_dsn: str, tmp_path: Path, monkeypatch) -> None:
    from wikimind.ingest.errors import IngestInputError

    def fake_add(conn, **kwargs):
        raise IngestInputError(
            "no project named 'nope'", hint="create it first with wikimind project create"
        )

    monkeypatch.setattr(ingest_cli, "add_documents", fake_add)
    source = tmp_path / "note.txt"
    source.write_text("text", encoding="utf-8")

    result = runner.invoke(root_app, ["add", "nope", str(source), "--dsn", test_dsn])

    assert result.exit_code == 2
    assert "no project named 'nope'" in result.output
    assert "hint: create it first" in result.output
