"""CLI surface tests: mounting, exit codes, and machine-readable output."""

from __future__ import annotations

import json

from integrity_seed import connect_db, seed_project
from typer.testing import CliRunner

from wikimind.cli import app

runner = CliRunner()


def _plain_help(result) -> str:
    # Strip ANSI and collapse whitespace so assertions are width-agnostic.
    return " ".join(result.output.replace("\x1b", "").split())


def test_integrity_commands_are_mounted() -> None:
    result = runner.invoke(app, ["integrity", "--help"])
    assert result.exit_code == 0, result.output
    text = _plain_help(result)
    for command in (
        "check",
        "import-costs",
        "verify-answer",
        "contradictions",
        "verify-ledger",
        "remove",
        "leaks",
    ):
        assert command in text


def test_verify_ledger_json_on_empty_database(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        seed_project(conn, "empty-ledger-cli")
    finally:
        conn.close()
    result = runner.invoke(
        app,
        [
            "integrity",
            "verify-ledger",
            "--project",
            "empty-ledger-cli",
            "--dsn",
            test_dsn,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["check"] == "ledger"
    assert payload["ok"] is True
    assert payload["counts"]["blocks"] == 0


def test_verify_answer_reports_malformed_json(tmp_path) -> None:
    path = tmp_path / "answer.json"
    path.write_text("{not json", encoding="utf-8")
    result = runner.invoke(app, ["integrity", "verify-answer", str(path), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["check"] == "answer"
    assert payload["ok"] is False
    assert any("invalid answer JSON" in finding for finding in payload["findings"])


def test_check_unknown_project_exits_two(test_dsn: str) -> None:
    result = runner.invoke(app, ["integrity", "check", "no-such-project", "--dsn", test_dsn])
    assert result.exit_code == 2


def test_import_missing_file_is_a_finding(test_dsn: str, tmp_path) -> None:
    result = runner.invoke(
        app,
        ["integrity", "import-costs", str(tmp_path / "absent.jsonl"), "--dsn", test_dsn, "--json"],
    )
    assert result.exit_code == 1
    assert "not found" in json.loads(result.output)["findings"][0]


def test_contradictions_human_summary(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        seed_project(conn, "empty-integrity-cli")
    finally:
        conn.close()
    result = runner.invoke(
        app, ["integrity", "contradictions", "empty-integrity-cli", "--dsn", test_dsn]
    )
    assert result.exit_code == 0, result.output
    assert "contradictions: clean" in result.output
    assert "edges.contradicts: 0" in result.output
