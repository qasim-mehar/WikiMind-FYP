"""V2d: durable job-state rows for compile runs.

The frozen ``jobs`` table (migration 001) supports the full state machine
``queued/running/done/failed/crashed``.  These writers use the three states
the compile pipeline actually reaches: ``running`` at start, then ``done`` or
``failed``.  ``payload`` carries the ``compile_runs.id`` so a job can be
resumed/inspected; no ``created_by_job`` references are rewritten.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

TERMINAL_STATES = frozenset({"done", "failed", "crashed"})
JOB_KIND_COMPILE = "compile"
JOB_KIND_REMOVAL = "removal"


def create_job(
    conn: psycopg.Connection,
    project_id: UUID | str,
    kind: str,
    payload: dict[str, Any] | None = None,
) -> UUID:
    """Insert a ``running`` job row and return its id."""

    row = conn.execute(
        "INSERT INTO jobs (project_id, kind, state, payload, heartbeat_at) "
        "VALUES (%s, %s, 'running', %s, now()) RETURNING id",
        (str(project_id), kind, Jsonb({} if payload is None else payload)),
    ).fetchone()
    return UUID(str(row["id"]))


def set_job_state(
    conn: psycopg.Connection,
    job_id: UUID | str,
    project_id: UUID | str,
    state: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Move a job to a terminal state and refresh its heartbeat."""

    if state not in TERMINAL_STATES:
        raise ValueError(f"unsupported terminal state {state!r}")
    conn.execute(
        "UPDATE jobs SET state = %s, heartbeat_at = now(), "
        "payload = payload || %s WHERE id = %s AND project_id = %s",
        (state, Jsonb({} if payload is None else payload), str(job_id), str(project_id)),
    )


def list_jobs(conn: psycopg.Connection, project_id: UUID | str) -> list[dict[str, Any]]:
    """Read-only snapshot of a project's jobs, newest first."""

    rows = conn.execute(
        "SELECT id, kind, state, payload, heartbeat_at, created_at "
        "FROM jobs WHERE project_id = %s ORDER BY created_at DESC",
        (str(project_id),),
    ).fetchall()
    return [dict(row) for row in rows]
