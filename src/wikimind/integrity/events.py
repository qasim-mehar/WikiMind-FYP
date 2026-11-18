"""V2a: append-only audit-event writer for the frozen ``events`` table.

Events are emitted from the compile pipeline (run start/finish and the
per-unit commit).  The writer is deliberately tiny: no updates, no deletes,
and no writer lock of its own when called from inside ``commit_unit`` (which
already holds the canonical per-project lock).  Use :func:`emit_event_locked`
for standalone writes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from wikimind.db.locks import with_writer_lock

RUN_STARTED = "compile.run_started"
RUN_FINISHED = "compile.run_finished"
UNIT_COMMITTED = "compile.unit_committed"

KNOWN_KINDS = frozenset({RUN_STARTED, RUN_FINISHED, UNIT_COMMITTED})


def emit_event(
    conn: psycopg.Connection,
    project_id: UUID | str,
    kind: str,
    data: dict[str, Any] | None = None,
) -> int:
    """Insert one event on ``conn``; the caller owns the transaction/lock.

    ``kind`` is validated against the known compile events so a typo cannot
    silently write an unusable audit row.  Unknown kinds raise ``ValueError``.
    """

    if not isinstance(kind, str) or kind not in KNOWN_KINDS:
        raise ValueError(f"unknown event kind {kind!r}; expected one of {sorted(KNOWN_KINDS)}")
    payload = {} if data is None else data
    row = conn.execute(
        "INSERT INTO events (project_id, kind, data) VALUES (%s, %s, %s) RETURNING id",
        (str(project_id), kind, Jsonb(payload)),
    ).fetchone()
    return int(row["id"])


def emit_event_locked(
    project_id: UUID | str,
    kind: str,
    data: dict[str, Any] | None = None,
) -> int:
    """Standalone writer: take the per-project lock in its own transaction."""

    with with_writer_lock(project_id) as conn:
        return emit_event(conn, project_id, kind, data)
