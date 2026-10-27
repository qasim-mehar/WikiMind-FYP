"""CLI tests for the compile lane: dry-run guard, project/document commands.

DB-backed cases use the conftest template protocol.  LLM-backed paths are
never exercised live here (the compile command requires WIKIMIND_LIVE=1).
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from typer.testing import CliRunner

from wikimind.cli import app as root_app

runner = CliRunner(env={"COLUMNS": "200"})


def test_compile_requires_live_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WIKIMIND_LIVE", raising=False)
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("WIKIMIND_DB", raising=False)
    result = runner.invoke(root_app, ["compile", "whatever"])

    assert result.exit_code == 2
    assert "WIKIMIND_LIVE=1" in result.output


def test_compile_help() -> None:
    result = runner.invoke(root_app, ["compile", "--help"])
    assert result.exit_code == 0
    assert "--min-score" in result.output
    assert "--delay" in result.output


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def test_project_create_and_list_roundtrip(test_dsn: str) -> None:
    created = runner.invoke(root_app, ["project", "create", "roundtrip-proj", "--dsn", test_dsn])
    assert created.exit_code == 0, created.output
    assert "created project roundtrip-proj" in created.output

    listed = runner.invoke(root_app, ["project", "list", "--dsn", test_dsn])
    assert listed.exit_code == 0
    assert "roundtrip-proj" in listed.output


def test_project_create_requires_dsn(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # keep the worktree .env out of get_settings()
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("WIKIMIND_DB", raising=False)

    result = runner.invoke(root_app, ["project", "create", "no-dsn"])

    assert result.exit_code == 2
    assert "no database DSN" in result.output


def test_document_add_and_list_roundtrip(test_dsn: str, tmp_path) -> None:
    project = runner.invoke(root_app, ["project", "create", "doc-proj", "--dsn", test_dsn])
    assert project.exit_code == 0

    source = tmp_path / "note.txt"
    source.write_text("Alpha joined Beta Labs in 2017.\n", encoding="utf-8")

    added = runner.invoke(root_app, ["document", "add", "doc-proj", str(source), "--dsn", test_dsn])
    assert added.exit_code == 0, added.output
    assert "registered: note.txt" in added.output
    assert "channels=1" in added.output

    # Re-adding the same bytes is an idempotent registration.
    again = runner.invoke(root_app, ["document", "add", "doc-proj", str(source), "--dsn", test_dsn])
    assert again.exit_code == 0
    assert "already registered: note.txt" in again.output

    listed = runner.invoke(root_app, ["document", "list", "doc-proj", "--dsn", test_dsn])
    assert listed.exit_code == 0
    assert "note.txt" in listed.output


def test_document_add_unknown_project_errors(test_dsn: str, tmp_path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("text", encoding="utf-8")
    result = runner.invoke(
        root_app, ["document", "add", "no-such-project", str(source), "--dsn", test_dsn]
    )
    assert result.exit_code == 2
    assert "no project named" in result.output
