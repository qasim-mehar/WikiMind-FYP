"""Lane 16 ingestion flow tests.

Every LLM call is a keyless fake; nothing touches a real provider.  DB cases
run against the conftest throwaway database, never the live corpus.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from wikimind.compile.graph import create_project
from wikimind.ingest import add_documents
from wikimind.ingest.errors import IngestConfigError, IngestInputError
from wikimind.llm import CostMeter, FakeLLM, InMemorySink, Usage

TEXT = "Alpha joined Beta Labs in 2017. Alpha leads the Delta team."
QUOTE = "Alpha joined Beta Labs in 2017."


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def _project(conn: psycopg.Connection) -> str:
    return create_project(conn, f"lane16-{uuid4().hex[:12]}").name


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "note.txt"
    path.write_text(TEXT, encoding="utf-8")
    return path


def _analyze() -> dict:
    return {
        "claims": [
            {
                "claim_id_hint": "c1",
                "text": QUOTE,
                "support_quote": QUOTE,
                "entities": [{"name": "Alpha", "type": "person"}],
                "relations": [],
                "confidence": "high",
                "notes": "",
            }
        ]
    }


def _write() -> dict:
    return {
        "disposition": "new",
        "target_claim_id": None,
        "text": QUOTE,
        "entities": [{"name": "Alpha", "type": "person"}],
        "relations": [],
        "notes": "",
    }


def _judge() -> dict:
    return {"entailed": True, "score": 5, "reason": "verbatim from the passage."}


def _fakes(*, with_script: bool = True):
    meter = CostMeter(InMemorySink())
    usage = Usage(100, 20)
    if with_script:
        drafter = FakeLLM([_analyze(), _write()], usage=usage, meter=meter)
        judge = FakeLLM([_judge()], usage=usage, meter=meter)
    else:
        drafter = FakeLLM([], usage=usage, meter=meter)
        judge = FakeLLM([], usage=usage, meter=meter)
    return drafter, judge, meter


def _claim_count(conn: psycopg.Connection, project_name: str) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM claims c JOIN projects p ON p.id = c.project_id "
        "WHERE p.name = %s",
        (project_name,),
    ).fetchone()
    return row["n"]


# ---------------------------------------------------------------------------
# H1: one flow, visible stage progress, friendly errors
# ---------------------------------------------------------------------------


def test_add_compiles_with_fakes_and_reports_stages(test_dsn: str, tmp_path: Path) -> None:
    conn = _connect(test_dsn)
    try:
        project = _project(conn)
        drafter, judge, meter = _fakes()
        messages: list[str] = []

        outcome = add_documents(
            conn,
            project_ref=project,
            files=[_source(tmp_path)],
            drafter=drafter,
            judge=judge,
            meter=meter,
            confirm=lambda _estimate: True,
            progress=messages.append,
        )

        assert outcome.compiled
        assert outcome.run is not None and outcome.run.status == "done"
        assert outcome.run.stats.claims_committed == 1
        # FakeLLM uses the $0-priced fake model; what matters is that every
        # call was metered and the total is surfaced from the same meter.
        assert len(meter.events) == 3
        assert outcome.shadow_cost_usd == meter.total_shadow_cost_usd
        assert _claim_count(conn, project) == 1
        joined = "\n".join(messages)
        assert "[1/3] extract" in joined
        assert "[2/3] estimate" in joined
        assert "[3/3] compile" in joined
        assert "[compile] call" in joined
    finally:
        conn.close()


def test_add_reports_readable_extraction_line(test_dsn: str, tmp_path: Path) -> None:
    conn = _connect(test_dsn)
    try:
        project = _project(conn)
        drafter, judge, meter = _fakes()
        messages: list[str] = []
        add_documents(
            conn,
            project_ref=project,
            files=[_source(tmp_path)],
            drafter=drafter,
            judge=judge,
            meter=meter,
            confirm=lambda _estimate: True,
            progress=messages.append,
        )
        assert "registered: note.txt" in "\n".join(messages)
        assert "parser=text channels=1" in "\n".join(messages)
    finally:
        conn.close()


def test_add_friendly_errors(test_dsn: str, tmp_path: Path) -> None:
    conn = _connect(test_dsn)
    try:
        with pytest.raises(IngestInputError, match="no project named"):
            add_documents(
                conn,
                project_ref="no-such-project",
                files=[_source(tmp_path)],
                do_compile=False,
            )
        with pytest.raises(IngestInputError, match="file not found"):
            add_documents(
                conn,
                project_ref=_project(conn),
                files=[tmp_path / "missing.txt"],
                do_compile=False,
            )
        unsupported = tmp_path / "data.csv"
        unsupported.write_text("a,b", encoding="utf-8")
        with pytest.raises(IngestInputError, match="no default parser"):
            add_documents(
                conn,
                project_ref=_project(conn),
                files=[unsupported],
                do_compile=False,
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# H2: idempotency (skip by default) and explicit --force
# ---------------------------------------------------------------------------


def test_readd_is_a_noop(test_dsn: str, tmp_path: Path) -> None:
    conn = _connect(test_dsn)
    try:
        project = _project(conn)
        source = _source(tmp_path)
        drafter, judge, meter = _fakes()
        first = add_documents(
            conn,
            project_ref=project,
            files=[source],
            drafter=drafter,
            judge=judge,
            meter=meter,
            confirm=lambda _estimate: True,
        )
        assert first.compiled

        # A second add with an EMPTY script fails loudly if any LLM call happens.
        drafter2, judge2, meter2 = _fakes(with_script=False)
        messages: list[str] = []
        second = add_documents(
            conn,
            project_ref=project,
            files=[source],
            drafter=drafter2,
            judge=judge2,
            meter=meter2,
            confirm=lambda _estimate: pytest.fail("no estimate confirmation expected"),
            progress=messages.append,
        )

        assert second.run is None
        assert second.skipped_reason == "already compiled (nothing to do)"
        assert not second.extracted[0].pending_units
        assert drafter2.calls == [] and judge2.calls == []
        assert _claim_count(conn, project) == 1
        assert "already registered: note.txt" in "\n".join(messages)
    finally:
        conn.close()


def test_force_recompiles_without_duplicate_claims(test_dsn: str, tmp_path: Path) -> None:
    conn = _connect(test_dsn)
    try:
        project = _project(conn)
        source = _source(tmp_path)
        first_drafter, first_judge, first_meter = _fakes()
        add_documents(
            conn,
            project_ref=project,
            files=[source],
            drafter=first_drafter,
            judge=first_judge,
            meter=first_meter,
            confirm=lambda _estimate: True,
        )

        markers = conn.execute(
            "SELECT count(*) AS n FROM compile_units c JOIN projects p ON p.id = c.project_id "
            "WHERE p.name = %s",
            (project,),
        ).fetchone()["n"]
        assert markers == 1

        drafter, judge, meter = _fakes()
        messages: list[str] = []
        outcome = add_documents(
            conn,
            project_ref=project,
            files=[source],
            drafter=drafter,
            judge=judge,
            meter=meter,
            force=True,
            confirm=lambda _estimate: True,
            progress=messages.append,
        )

        assert outcome.compiled
        # Re-distillation re-runs ANALYZE, but the deterministic claim is
        # already known, so dedup stops it before WRITE/JUDGE: no duplicates.
        assert len(drafter.calls) == 1 and judge.calls == []
        assert "cleared 1 unit marker(s)" in "\n".join(messages)
        assert _claim_count(conn, project) == 1  # deterministic ids, no duplicates
        after = conn.execute(
            "SELECT count(*) AS n FROM compile_units c JOIN projects p ON p.id = c.project_id "
            "WHERE p.name = %s",
            (project,),
        ).fetchone()["n"]
        assert after == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# H3: estimate + confirmation gate
# ---------------------------------------------------------------------------


def test_declined_estimate_makes_no_llm_calls(test_dsn: str, tmp_path: Path) -> None:
    conn = _connect(test_dsn)
    try:
        project = _project(conn)
        drafter, judge, meter = _fakes(with_script=False)
        messages: list[str] = []
        outcome = add_documents(
            conn,
            project_ref=project,
            files=[_source(tmp_path)],
            drafter=drafter,
            judge=judge,
            meter=meter,
            confirm=lambda _estimate: False,
            progress=messages.append,
        )
        assert outcome.skipped_reason == "declined by user"
        assert outcome.estimate is not None and outcome.estimate.units == 1
        assert drafter.calls == [] and judge.calls == []
        assert _claim_count(conn, project) == 0
        assert "no LLM calls made" in "\n".join(messages)
    finally:
        conn.close()


def test_live_flag_required_without_injected_clients(
    test_dsn: str, tmp_path: Path, monkeypatch
) -> None:
    conn = _connect(test_dsn)
    try:
        monkeypatch.delenv("WIKIMIND_LIVE", raising=False)
        with pytest.raises(IngestConfigError, match="WIKIMIND_LIVE=1"):
            add_documents(
                conn,
                project_ref=_project(conn),
                files=[_source(tmp_path)],
                confirm=lambda _estimate: True,
                meter=CostMeter(InMemorySink()),
            )
    finally:
        conn.close()


def test_no_compile_registers_without_estimate(test_dsn: str, tmp_path: Path) -> None:
    conn = _connect(test_dsn)
    try:
        outcome = add_documents(
            conn,
            project_ref=_project(conn),
            files=[_source(tmp_path)],
            do_compile=False,
        )
        assert outcome.estimate is None
        assert outcome.skipped_reason == "compile disabled (--no-compile)"
        assert outcome.new_documents == 1
        assert _claim_count(conn, outcome.project_name) == 0
    finally:
        conn.close()
