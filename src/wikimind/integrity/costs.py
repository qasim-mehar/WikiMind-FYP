"""V2b/V3: cost_events mirror and historical importer.

``costs.jsonl`` stays the append-of-record (Ruling 11 shadow pricing is
untouched, and this package never rewrites a ledger line).  Every emitted
event is additionally mirrored into the frozen ``cost_events`` table when a
DSN is configured, and :func:`import_costs` backfills an existing JSONL file.

Both writers derive the row id from the **redacted JSON line exactly as
written to disk** (``JsonlCostSink`` applies ``redact()``), via
:func:`wikimind.integrity.ids.cost_event_uuid`, so mirroring an event and
later importing its JSONL file produce the same id and re-importing is a
no-op (``ON CONFLICT (id) DO NOTHING``).  The nested
``unit_price`` object from the JSONL is preserved losslessly in ``meta``; the
scalar ``unit_price`` column holds its ``input_per_mtok`` component because
the frozen column is NUMERIC, not JSONB.  NUMERIC(20,10) cannot represent the
ledger's 12-decimal shadow cost exactly, so that value is quantized to 10
decimals on write; the full-precision value stays in ``meta``.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from wikimind.db.connection import connect
from wikimind.db.locks import with_writer_lock
from wikimind.llm.cost import CostEvent, CostMeter, CostSink, JsonlCostSink, read_cost_events
from wikimind.llm.errors import CostSinkError
from wikimind.llm.secrets import redact

from .ids import cost_event_uuid
from .model import IntegrityReport

MIRROR_ENV_VAR = "WIKIMIND_COST_DB_MIRROR"
NUMERIC_10 = Decimal("0.0000000001")

INSERT_COST_EVENT = """
    INSERT INTO cost_events
        (id, project_id, job_id, call_site, model, in_tokens, out_tokens,
         unit_price, shadow_cost_usd, meta, created_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING
"""


def canonical_event_json(event: CostEvent) -> str:
    """The exact (redacted) JSON line that :class:`JsonlCostSink` appends."""

    return redact(event.model_dump_json())


def resolve_project_id(
    conn: psycopg.Connection,
    event: CostEvent,
    explicit: UUID | str | None = None,
) -> UUID | None:
    """Resolve a cost event to its project.

    Compile events carry ``meta.run`` (a ``compile_runs.id``); smoke events
    carry none and need an explicit project to be imported.
    """

    if explicit is not None:
        return UUID(str(explicit))
    run = event.meta.get("run")
    if not isinstance(run, str) or not run:
        return None
    row = conn.execute(
        "SELECT project_id FROM compile_runs WHERE id::text = %s",
        (run,),
    ).fetchone()
    return None if row is None else UUID(str(row["project_id"]))


def cost_event_values(event: CostEvent, project_id: UUID | str) -> dict[str, Any]:
    """Map one cost event onto the frozen ``cost_events`` columns."""

    identity = str(cost_event_uuid(canonical_event_json(event)))
    meta = dict(event.meta)
    meta.update(
        {
            "unit_price": event.unit_price.model_dump(mode="json"),
            "shadow_cost_usd_full": str(event.shadow_cost_usd),
            "source_hash": identity,
            "origin": "costs.jsonl",
        }
    )
    return {
        "id": identity,
        "project_id": str(project_id),
        "job_id": None,  # V0 compile runs are not jobs rows (see graph.py).
        "call_site": event.call_site,
        "model": event.model,
        "in_tokens": int(event.in_tokens),
        "out_tokens": int(event.out_tokens),
        "unit_price": Decimal(str(event.unit_price.input_per_mtok)),
        "shadow_cost_usd": Decimal(str(event.shadow_cost_usd)).quantize(NUMERIC_10),
        "meta": Jsonb(meta),
        "created_at": event.timestamp,
    }


def insert_cost_event(conn: psycopg.Connection, event: CostEvent, project_id: UUID | str) -> bool:
    """Insert one mirrored cost row.  Returns True only when it was new."""

    values = cost_event_values(event, project_id)
    params = tuple(
        values[key]
        for key in (
            "id",
            "project_id",
            "job_id",
            "call_site",
            "model",
            "in_tokens",
            "out_tokens",
            "unit_price",
            "shadow_cost_usd",
            "meta",
            "created_at",
        )
    )
    cursor = conn.execute(INSERT_COST_EVENT, params)
    return cursor.rowcount == 1


class MirroringCostSink:
    """Append to the JSONL ledger first, then mirror the same event to the DB.

    The mirror is best-effort: a database failure is counted, never raised, so
    a billable call can never be lost from the append-of-record ledger because
    of this lane.  A DSN must be supplied for mirroring to be active.
    """

    def __init__(
        self,
        primary: CostSink,
        *,
        dsn: str | None = None,
        project_id: UUID | str | None = None,
    ) -> None:
        self.primary = primary
        self.dsn = dsn
        self.project_id = None if project_id is None else UUID(str(project_id))
        self.mirrored = 0
        self.skipped = 0
        self.failures = 0

    def preflight(self) -> None:
        self.primary.preflight()

    def emit(self, event: CostEvent) -> None:
        self.primary.emit(event)
        if not self.dsn:
            return
        try:
            with connect(self.dsn, row_factory=dict_row, autocommit=True) as conn:
                # Resolve first: the advisory lock key must be this event's
                # project, never a global dummy key (M1/G2).
                project_id = self.project_id or resolve_project_id(conn, event)
                if project_id is None:
                    self.skipped += 1
                    return
                with with_writer_lock(project_id, conn):
                    if insert_cost_event(conn, event, project_id):
                        self.mirrored += 1
        except Exception:  # noqa: BLE001 - mirroring must not mask the JSONL write
            self.failures += 1


def build_meter(
    costs_path: str | Path | None = None,
    *,
    dsn: str | None = None,
    environ: dict[str, str] | None = None,
) -> CostMeter:
    """Build the compile meter, enabling the DB mirror only when opted in.

    ``WIKIMIND_COST_DB_MIRROR=1`` is required; live activation is post-merge,
    so the default behaviour is byte-for-byte the old JSONL-only meter.
    """

    sink: CostSink = JsonlCostSink(costs_path)
    env = os.environ if environ is None else environ
    if env.get(MIRROR_ENV_VAR) == "1" and dsn:
        sink = MirroringCostSink(sink, dsn=dsn)
    return CostMeter(sink)


def import_costs(
    path: str | Path,
    *,
    dsn: str | None = None,
    project_id: UUID | str | None = None,
    conn: psycopg.Connection | None = None,
) -> IntegrityReport:
    """Load a historical costs.jsonl into ``cost_events``, idempotently."""

    report = IntegrityReport("cost-import")
    if not Path(path).is_file():
        report.finding(f"cost ledger not found: {path}")
        return report
    try:
        events = read_cost_events(path)
    except (CostSinkError, OSError, ValueError) as exc:
        report.finding(f"could not read cost ledger: {exc}")
        return report

    report.set_count("events_read", len(events))
    owns = conn is None
    active = conn if conn is not None else connect(dsn, row_factory=dict_row, autocommit=True)
    try:
        for event in events:
            try:
                resolved = resolve_project_id(active, event, project_id)
            except (ValueError, psycopg.Error) as exc:
                report.finding(f"could not resolve project for {event.call_site}: {exc}")
                report.count("errors", 1)
                continue
            if resolved is None:
                report.count("skipped_no_project", 1)
                continue
            with with_writer_lock(resolved, active):
                if insert_cost_event(active, event, resolved):
                    report.count("inserted", 1)
                else:
                    report.count("already_present", 1)
    finally:
        if owns:
            active.close()
    return report
