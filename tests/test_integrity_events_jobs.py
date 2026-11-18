"""V2a/V2d: audit events and durable job rows (fakes + compile wiring)."""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest
from integrity_seed import connect_db, seed_project
from psycopg.types.json import Jsonb
from test_compile_pipeline import PASSAGE, ScriptedLLM, _happy_script, _register

from wikimind.compile import compile_project, resolve_project
from wikimind.compile.graph import AcceptedClaim, Unit, commit_unit, create_project
from wikimind.compile.models import ProposedEntity
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


def test_compile_retry_skips_duplicate_unit_event_and_anchor(test_dsn: str) -> None:
    """G5: a repeated commit writes neither a second anchor nor a second event."""

    conn = connect_db(test_dsn)
    try:
        project = create_project(conn, f"integrity-retry-{uuid4().hex[:8]}")
        _register(conn, project.id, "doc1.txt", PASSAGE)
        drafter, judge = ScriptedLLM(), ScriptedLLM()
        _happy_script(drafter, judge)
        compile_project(
            conn, project=resolve_project(conn, str(project.id)), drafter=drafter, judge=judge
        )
        document = conn.execute(
            "SELECT id, sha256 FROM documents WHERE project_id = %s", (str(project.id),)
        ).fetchone()
        channel = conn.execute(
            "SELECT id FROM channels WHERE document_id = %s", (str(document["id"]),)
        ).fetchone()
        unit = Unit(
            document_id=document["id"],
            document_sha256=document["sha256"],
            channel_id=channel["id"],
            kind="section",
            ordinal=0,
            raw_text=PASSAGE,
        )
        accepted = [
            AcceptedClaim(
                text="Alpha joined Beta Labs in 2017.",
                support_quote="Alpha joined Beta Labs in 2017.",
                entities=(ProposedEntity(name="Alpha", type="person"),),
                relations=(),
                disposition="new",
                target_claim_id=None,
            )
        ]
        commit_unit(conn, project_id=project.id, unit=unit, accepted=accepted)
        events = conn.execute(
            "SELECT count(*) AS n FROM events WHERE project_id = %s AND kind = %s",
            (str(project.id), UNIT_COMMITTED),
        ).fetchone()["n"]
        anchors = conn.execute(
            "SELECT count(*) AS n FROM ledger_blocks WHERE project_id = %s AND block_type = 'derived'",
            (str(project.id),),
        ).fetchone()["n"]
        assert events == 1
        assert anchors == 1
        assert verify_ledger(conn, project.id).ok
    finally:
        conn.close()


def test_finish_failure_still_terminalizes_the_job(test_dsn: str, monkeypatch) -> None:
    """G7: a finish-time DB failure never leaves a job running silently."""

    conn = connect_db(test_dsn)
    try:
        project = create_project(conn, f"integrity-finish-{uuid4().hex[:8]}")
        _register(conn, project.id, "doc1.txt", PASSAGE)
        drafter, judge = ScriptedLLM(), ScriptedLLM()
        _happy_script(drafter, judge)
        real_execute = conn.execute
        failed = []

        def flaky_execute(query, params=None, **kwargs):
            if "UPDATE compile_runs" in str(query) and not failed:
                failed.append(True)
                raise psycopg.OperationalError("forced finish failure")
            return real_execute(query, params, **kwargs)

        monkeypatch.setattr(conn, "execute", flaky_execute)
        with pytest.raises(psycopg.OperationalError):
            compile_project(
                conn, project=resolve_project(conn, str(project.id)), drafter=drafter, judge=judge
            )
    finally:
        conn.close()

    check = connect_db(test_dsn)
    try:
        jobs = list_jobs(check, project.id)
        assert len(jobs) == 1
        assert jobs[0]["state"] == "failed"
        kinds = [
            row["kind"]
            for row in check.execute(
                "SELECT kind FROM events WHERE project_id = %s ORDER BY id", (str(project.id),)
            ).fetchall()
        ]
        assert RUN_FINISHED in kinds
    finally:
        check.close()


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
