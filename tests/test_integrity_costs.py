"""V2b/V3: cost_events mirror and idempotent historical import."""

from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal
from uuid import uuid4

from integrity_seed import connect_db, seed_project, seed_run

from wikimind.integrity.costs import (
    MIRROR_ENV_VAR,
    MirroringCostSink,
    build_meter,
    canonical_event_json,
    cost_event_values,
    import_costs,
    insert_cost_event,
    resolve_project_id,
)
from wikimind.integrity.ids import cost_event_uuid
from wikimind.llm.cost import CostMeter, InMemorySink, JsonlCostSink
from wikimind.prices import GEMINI_MODEL, JUDGE_MODEL, price_for


def _event(model: str = GEMINI_MODEL, meta: dict | None = None):
    meter = CostMeter(InMemorySink())
    return meter.record(
        call_site="compile.analyze",
        model=model,
        in_tokens=100,
        out_tokens=50,
        meta=meta or {},
    )


def test_build_meter_defaults_to_jsonl_only(tmp_path) -> None:
    meter = build_meter(tmp_path / "costs.jsonl", dsn="postgresql://unused", environ={})
    assert type(meter.sink) is JsonlCostSink
    meter = build_meter(
        tmp_path / "costs.jsonl", dsn="postgresql://unused", environ={MIRROR_ENV_VAR: "1"}
    )
    assert isinstance(meter.sink, MirroringCostSink)


def test_cost_values_keep_ruling_11_shadow_pricing_and_full_precision() -> None:
    event = _event(model=JUDGE_MODEL)
    values = cost_event_values(event, uuid4())
    assert values["model"] == JUDGE_MODEL
    assert values["shadow_cost_usd"] > 0
    assert Decimal(values["unit_price"]) == Decimal(str(price_for(JUDGE_MODEL).input_per_mtok))
    assert price_for(JUDGE_MODEL).input_per_mtok > 0  # Ruling 11: :free priced at paid twin
    assert values["meta"].obj["shadow_cost_usd_full"] == str(event.shadow_cost_usd)
    assert values["id"] == str(cost_event_uuid(canonical_event_json(event)))


def test_mirror_is_idempotent_and_resolves_project_from_run(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        run = seed_run(conn, project)
        primary = InMemorySink()
        mirror = MirroringCostSink(primary, dsn=test_dsn)
        event = _event(meta={"run": str(run)})
        mirror.emit(event)
        mirror.emit(event)
        assert len(primary.events) == 2  # both emits reached the append-of-record sink
        assert mirror.mirrored == 1  # only the first was new in the DB mirror
        assert mirror.failures == 0
        rows = conn.execute(
            "SELECT project_id, call_site, meta FROM cost_events WHERE id = %s",
            (str(cost_event_uuid(canonical_event_json(event))),),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["project_id"] == project
        assert rows[0]["meta"]["run"] == str(run)
    finally:
        conn.close()


def test_mirror_without_dsn_is_a_noop() -> None:
    mirror = MirroringCostSink(InMemorySink(), dsn=None)
    mirror.emit(_event())
    assert mirror.skipped == 0
    assert mirror.failures == 0


def test_import_is_idempotent_and_skips_runless_events(test_dsn: str, tmp_path) -> None:
    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        run = seed_run(conn, project)
        ledger = tmp_path / "costs.jsonl"
        sink = JsonlCostSink(ledger)
        compile_event = _event(meta={"run": str(run), "stage": "analyze"})
        smoke_event = _event(meta={"provider": "smoke"})
        sink.emit(compile_event)
        sink.emit(smoke_event)

        first = import_costs(ledger, dsn=test_dsn)
        assert first.counts["inserted"] == 1
        assert first.counts["skipped_no_project"] == 1
        second = import_costs(ledger, dsn=test_dsn)
        assert second.counts.get("inserted", 0) == 0
        assert second.counts["already_present"] == 1
        assert second.ok

        # An explicit project also imports the run-less smoke line.
        third = import_costs(ledger, dsn=test_dsn, project_id=project)
        assert third.counts["inserted"] == 1
        imported_ids = (
            str(cost_event_uuid(canonical_event_json(compile_event))),
            str(cost_event_uuid(canonical_event_json(smoke_event))),
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM cost_events WHERE id::text = ANY(%s)",
                (list(imported_ids),),
            ).fetchone()["n"]
            == 2
        )
    finally:
        conn.close()


def test_resolve_project_and_insert_use_frozen_columns(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        unknown = uuid4()
        assert resolve_project_id(conn, _event(), unknown) == unknown
        assert resolve_project_id(conn, _event()) is None
        event = _event()
        assert insert_cost_event(conn, event, project) is True
        assert insert_cost_event(conn, event, project) is False
        row = conn.execute(
            "SELECT job_id, in_tokens, out_tokens, unit_price, shadow_cost_usd, created_at "
            "FROM cost_events"
        ).fetchone()
        assert row["job_id"] is None
        assert row["in_tokens"] == 100
        assert row["out_tokens"] == 50
        assert row["created_at"] is not None
    finally:
        conn.close()


def test_mirror_takes_the_project_lock_key(test_dsn: str, monkeypatch) -> None:
    """G2: the advisory key is the resolved project, never the zero UUID."""

    import wikimind.integrity.costs as costs_module

    real_lock = costs_module.with_writer_lock
    keys: list[str] = []

    @contextmanager
    def spy_lock(project_id, conn=None):
        keys.append(str(project_id))
        with real_lock(project_id, conn) as active:
            yield active

    monkeypatch.setattr(costs_module, "with_writer_lock", spy_lock)
    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        run = seed_run(conn, project)
    finally:
        conn.close()

    mirror = MirroringCostSink(InMemorySink(), dsn=test_dsn)
    mirror.emit(_event(meta={"run": str(run)}))
    assert keys == [str(project)]
    assert mirror.failures == 0


def test_mirror_failure_surfaces_on_stderr(capsys) -> None:
    """G13a: mirror errors are counted on the sink and warned about, never raised."""

    mirror = MirroringCostSink(InMemorySink(), dsn="not-a-dsn", project_id=uuid4())
    mirror.emit(_event())
    assert mirror.failures == 1
    assert "warning: cost_events mirror failed" in capsys.readouterr().err


def test_import_hashes_the_literal_line(test_dsn: str, tmp_path) -> None:
    """G13c: import ids come from the exact on-disk string, not a re-serialization."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        run = seed_run(conn, project)
    finally:
        conn.close()

    event = _event(meta={"run": str(run)})
    canonical = canonical_event_json(event)
    raw = json.loads(canonical)
    reordered = json.dumps(
        {
            "meta": raw["meta"],
            "call_site": raw["call_site"],
            "model": raw["model"],
            "in_tokens": raw["in_tokens"],
            "out_tokens": raw["out_tokens"],
            "unit_price": raw["unit_price"],
            "shadow_cost_usd": raw["shadow_cost_usd"],
            "timestamp": raw["timestamp"],
        },
        separators=(",", ":"),
    )
    ledger = tmp_path / "literal.jsonl"
    ledger.write_text(f"{reordered}\n{canonical}\n", encoding="utf-8")

    first = import_costs(ledger, dsn=test_dsn)
    assert first.counts["inserted"] >= 2
    second = import_costs(ledger, dsn=test_dsn)
    assert second.counts.get("inserted", 0) == 0

    conn = connect_db(test_dsn)
    try:
        for line in (reordered, canonical):
            rows = conn.execute(
                "SELECT count(*) AS n FROM cost_events WHERE id = %s",
                (str(cost_event_uuid(line)),),
            ).fetchone()["n"]
            assert rows == 1
    finally:
        conn.close()


def test_mirror_and_import_share_the_redacted_hash(test_dsn: str, tmp_path) -> None:
    """G4: both writers hash the redacted JSONL line, so import adds no row."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        run = seed_run(conn, project)
    finally:
        conn.close()

    ledger = tmp_path / "costs.jsonl"
    event = _event(meta={"run": str(run), "api_key": "sk-or-v1-" + "A" * 24})
    mirror = MirroringCostSink(JsonlCostSink(ledger), dsn=test_dsn)
    mirror.emit(event)
    assert mirror.mirrored == 1

    line = ledger.read_text(encoding="utf-8").strip()
    assert "[REDACTED]" in line
    assert "sk-or-v1-" not in line
    identity = str(cost_event_uuid(line))

    result = import_costs(ledger, dsn=test_dsn)
    assert result.counts.get("inserted", 0) == 0
    assert result.counts["already_present"] >= 1
    conn = connect_db(test_dsn)
    try:
        rows = conn.execute(
            "SELECT count(*) AS n FROM cost_events WHERE id = %s", (identity,)
        ).fetchone()["n"]
        assert rows == 1
    finally:
        conn.close()
