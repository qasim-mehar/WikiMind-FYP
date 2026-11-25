"""Lane 16 CLI tests for ``wikimind add``.

No live provider is ever used: the confirmation and flow paths are wired with
a stubbed ``add_documents``; the extraction-only path runs against the sandbox
throwaway database created by conftest.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from wikimind.cli import app as root_app
from wikimind.compile.graph import create_project
from wikimind.ingest import IngestOutcome, estimate_compile_cost
from wikimind.ingest import cli as ingest_cli

runner = CliRunner(env={"COLUMNS": "200"})


def test_add_help() -> None:
    result = runner.invoke(root_app, ["add", "--help"])
    assert result.exit_code == 0
    flat = result.output.replace("\n", "")
    for option in ("--force", "--yes", "--no-compile", "--claims-per-unit", "--create-project"):
        assert option in flat


def test_add_no_dsn_reports_friendly_error(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # keep the worktree .env out of get_settings()
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("WIKIMIND_DB", raising=False)
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
    assert "yes" not in captured  # the flag is consumed into the confirm callback


def test_add_default_prompt_can_be_declined(test_dsn: str, tmp_path: Path, monkeypatch) -> None:
    def fake_add(conn, **kwargs):
        estimate = estimate_compile_cost(["passage"])
        assert kwargs["confirm"](estimate) is False
        return IngestOutcome(
            project_name="stub-project",
            extracted=[],
            skipped_reason="declined by user",
        )

    monkeypatch.setattr(ingest_cli, "add_documents", fake_add)
    source = tmp_path / "note.txt"
    source.write_text("text", encoding="utf-8")

    result = runner.invoke(
        root_app,
        ["add", "proj", str(source), "--dsn", test_dsn],
        input="n\n",
    )

    assert result.exit_code == 0, result.output
    assert "Proceed with compile" in result.output
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
