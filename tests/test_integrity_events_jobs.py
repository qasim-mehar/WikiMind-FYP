"""V2a/V2d: audit events and durable job rows (fakes + compile wiring)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb

from tests.integrity_seed import connect_db, seed_project
from tests.test_compile_pipeline import PASSAGE, ScriptedLLM, _happy_script, _register
from wikimind.compile import compile_project, resolve_project
from wikimind.compile.graph import create_project
from wikimind.integrity.events import (
    RUN_FINISHED,
    RUN_STARTED,
    UNIT_COMMITTED,
    emit_event,
)
from wikimind.integrity.jobs import create_job, list_jobs, set_job_state
from wikimind.integrity.ledger import verify_ledger


class _Cursor:
    def __init__(self, row: dict) -> None:
        self.row = row

    def fetchone(self) -> dict:
        return self.row


class FakeConn:
    def __init__(self, fake_id: str | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.fake_id = fake_id or str(uuid4())

    def execute(self, sql: str, params: tuple) -> _Cursor:
        self.calls.append((sql, params))
        return _Cursor({"id": self.fake_id})


def test_event_writer_uses_the_frozen_columns() -> None:
    conn = FakeConn(fake_id="42")
    project = uuid4()
    event_id = emit_event(conn, project, UNIT_COMMITTED, {"run_id": "r1"})
    assert event_id == 42
    sql, params = conn.calls[0]
    assert "INSERT INTO events (project_id, kind, data)" in sql
    assert params[0] == str(project)
    assert params[1] == UNIT_COMMITTED
    assert isinstance(params[2], Jsonb)
    assert params[2].obj == {"run_id": "r1"}


def test_event_kinds_are_validated() -> None:
    with pytest.raises(ValueError, match="unknown event kind"):
        emit_event(FakeConn(), uuid4(), "typo.kind")


def test_job_writer_uses_the_frozen_state_machine() -> None:
    conn = FakeConn()
    project = uuid4()
    job_id = create_job(conn, project, "compile", {"run_id": "r"})
    assert isinstance(job_id, UUID)
    insert_sql, insert_params = conn.calls[0]
    assert "INSERT INTO jobs" in insert_sql
    assert "'running'" in insert_sql
    assert insert_params[1] == "compile"

    set_job_state(conn, job_id, project, "done", {" ok": 1})
    update_sql, update_params = conn.calls[1]
    assert "UPDATE jobs SET state" in update_sql
    assert update_params[0] == "done"
    with pytest.raises(ValueError, match="unsupported terminal state"):
        set_job_state(conn, job_id, project, "bogus")


def test_compile_wires_events_jobs_and_the_ledger(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project = create_project(conn, f"integrity-wiring-{uuid4().hex[:8]}")
        _register(conn, project.id, "doc1.txt", PASSAGE)
        drafter, judge = ScriptedLLM(), ScriptedLLM()
        _happy_script(drafter, judge)
        result = compile_project(
            conn, project=resolve_project(conn, str(project.id)), drafter=drafter, judge=judge
        )
        assert result.status == "done"

        kinds = [
            row["kind"]
            for row in conn.execute(
                "SELECT kind FROM events WHERE project_id = %s ORDER BY id", (str(project.id),)
            ).fetchall()
        ]
        assert kinds == [RUN_STARTED, UNIT_COMMITTED, RUN_FINISHED]
        unit_event = conn.execute(
            "SELECT data FROM events WHERE project_id = %s AND kind = %s",
            (str(project.id), UNIT_COMMITTED),
        ).fetchone()["data"]
        assert len(unit_event["channel_id"]) >= 8
        assert len(unit_event["claim_ids"]) == 1
        assert unit_event["counts"]["claims"] == 1
        assert len(unit_event["ledger_block_ids"]) == 2

        jobs = list_jobs(conn, project.id)
        assert len(jobs) == 1
        assert jobs[0]["state"] == "done"
        assert jobs[0]["payload"]["run_id"] == str(result.run_id)

        ledger = verify_ledger(conn, project.id)
        assert ledger.ok, ledger.findings
        assert ledger.counts["claim_blocks"] == 1
    finally:
        conn.close()


def test_failed_run_marks_the_job_failed(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project = create_project(conn, f"integrity-fail-{uuid4().hex[:8]}")
        _register(conn, project.id, "doc1.txt", PASSAGE)
        # No scripted responses: the analyze stage raises, so the run fails.
        drafter, judge = ScriptedLLM(), ScriptedLLM()
        with pytest.raises(AssertionError):
            compile_project(
                conn, project=resolve_project(conn, str(project.id)), drafter=drafter, judge=judge
            )
        jobs = list_jobs(conn, project.id)
        assert jobs[0]["state"] == "failed"
        kinds = [
            row["kind"]
            for row in conn.execute(
                "SELECT kind FROM events WHERE project_id = %s ORDER BY id", (str(project.id),)
            ).fetchall()
        ]
        assert kinds == [RUN_STARTED, RUN_FINISHED]
    finally:
        conn.close()


def test_event_writer_on_real_db(test_dsn: str) -> None:
    from wikimind.db.locks import with_writer_lock

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        with with_writer_lock(project, conn):
            event_id = emit_event(conn, project, RUN_STARTED, {"note": "standalone"})
        row = conn.execute("SELECT kind, data FROM events WHERE id = %s", (event_id,)).fetchone()
        assert row["kind"] == RUN_STARTED
        assert row["data"] == {"note": "standalone"}
    finally:
        conn.close()
