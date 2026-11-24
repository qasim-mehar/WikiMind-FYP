"""Hand-built CLI integration test for the removal workflow (Ruling 23).

Drives the REAL Typer entry point against a template-spawned database with
no API keys and no network.  Removal is zero-LLM: the costs ledger path is
pointed at a temp file and must stay absent.  This test landed before the
removal command and was demonstrated to fail on the broken shape.

FAIL-FIRST EVIDENCE (pre-fix tree at a1ed2e9, 2026-11-24): before any removal
code or command existed, `pytest tests/test_integrity_removal_cli.py` reported
`1 failed in 5.78s`, exit code 1, with `No such command 'remove'.` from the real
Typer app and `assert 2 == 0` on `result.exit_code`. After the fix the same
test reports `1 passed`.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import psycopg
from integrity_seed import seed_channel, seed_claim, seed_document, seed_edge, seed_project
from psycopg.rows import dict_row
from typer.testing import CliRunner

from wikimind.cli import app

SHA = "a" * 64


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def _seed_compiled_reference(
    conn: psycopg.Connection, project_id: UUID, sha: str
) -> tuple[UUID, UUID, UUID]:
    """Hand-build one compiled reference node: document, claim, edge, ledger anchor."""

    from wikimind.compile.ids import citation_node_slug
    from wikimind.db.locks import with_writer_lock
    from wikimind.integrity.ledger import append_ledger_batch

    document = seed_document(conn, project_id, sha256=sha, source_uri="memo://paper-a.pdf")
    seed_channel(conn, project_id, document, channel_id="ch-a")
    slug = citation_node_slug(sha)
    node = UUID(
        str(
            conn.execute(
                "INSERT INTO nodes (project_id, slug, node_type) VALUES (%s, %s, 'citation') "
                "RETURNING id",
                (str(project_id), slug),
            ).fetchone()["id"]
        )
    )
    version = conn.execute(
        "INSERT INTO node_versions (project_id, node_id, version, rendered_text) "
        "VALUES (%s, %s, 1, %s) RETURNING id",
        (str(project_id), str(node), f"{slug} [citation]"),
    ).fetchone()
    conn.execute(
        "UPDATE nodes SET current_version_id = %s WHERE id = %s",
        (str(version["id"]), str(node)),
    )
    conn.execute(
        "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s)",
        (str(project_id), str(node), str(document)),
    )
    claim = seed_claim(conn, project_id, node, "ch-a", document)
    seed_edge(conn, project_id, node, node, claims=(claim,))
    with with_writer_lock(project_id, conn):
        append_ledger_batch(conn, project_id, node, [(node, claim)])
    return document, node, claim


def test_remove_tombstone_end_to_end(test_dsn: str, tmp_path: Path) -> None:
    """The real CLI tombstones a reference node, appends events, and leaks nothing."""

    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-cli-tombstone")
        _, reference, _ = _seed_compiled_reference(conn, project, SHA)
        assert (
            conn.execute("SELECT status FROM nodes WHERE id = %s", (str(reference),)).fetchone()[
                "status"
            ]
            == "active"
        )
    finally:
        conn.close()

    costs = tmp_path / "costs.jsonl"
    runner = CliRunner(
        env={
            "COLUMNS": "200",
            "WIKIMIND_LIVE": "",
            "GEMINI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
            "WIKIMIND_COSTS_PATH": str(costs),
        }
    )
    result = runner.invoke(
        app,
        [
            "integrity",
            "remove",
            "removal-cli-tombstone",
            SHA,
            "--mode",
            "tombstone",
            "--dsn",
            test_dsn,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["check"] == "removal"
    assert payload["ok"] is True, payload
    assert payload["counts"]["removals.done"] == 1
    assert payload["counts"]["leaks.total"] == 0

    conn = _connect(test_dsn)
    try:
        assert (
            conn.execute("SELECT status FROM nodes WHERE id = %s", (str(reference),)).fetchone()[
                "status"
            ]
            == "archived"
        )
        events = conn.execute(
            "SELECT kind FROM events WHERE project_id = %s ORDER BY id", (str(project),)
        ).fetchall()
        assert [row["kind"] for row in events][-1] == "integrity.removal_completed"
        job = conn.execute(
            "SELECT state, kind FROM jobs WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            (str(project),),
        ).fetchone()
        assert job["kind"] == "removal" and job["state"] == "done"
        # Every node write materialized a new immutable version (Ruling 14).
        versions = conn.execute(
            "SELECT count(*) AS n FROM node_versions WHERE node_id = %s", (str(reference),)
        ).fetchone()["n"]
        assert versions == 2
    finally:
        conn.close()

    # Zero LLM calls: no cost ledger side effect, no keys required.
    assert not costs.exists()


def test_leaks_command_surfaces_retracted_only_claims(test_dsn: str) -> None:
    """The read-only lint command flags claims supported only by retracted docs."""

    sha = "9" * 64
    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-cli-retracted")
        _seed_compiled_reference(conn, project, sha)
    finally:
        conn.close()

    runner = CliRunner()
    assert (
        runner.invoke(
            app,
            [
                "integrity",
                "remove",
                "removal-cli-retracted",
                sha,
                "--mode",
                "retracted",
                "--dsn",
                test_dsn,
                "--json",
            ],
        ).exit_code
        == 0
    )
    result = runner.invoke(
        app, ["integrity", "leaks", "removal-cli-retracted", "--dsn", test_dsn, "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["check"] == "leaks"
    assert payload["counts"]["claims.retracted_only"] >= 1
    assert any("retracted documents" in warning for warning in payload["warnings"])
