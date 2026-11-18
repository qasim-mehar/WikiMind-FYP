"""V2b/V3: cost_events mirror and idempotent historical import."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from integrity_seed import connect_db, seed_project, seed_run

from wikimind.integrity.costs import (
    MIRROR_ENV_VAR,
    MirroringCostSink,
    build_meter,
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
    assert values["id"] == str(cost_event_uuid(event.model_dump_json()))


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
            (str(cost_event_uuid(event.model_dump_json())),),
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
            str(cost_event_uuid(compile_event.model_dump_json())),
            str(cost_event_uuid(smoke_event.model_dump_json())),
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
