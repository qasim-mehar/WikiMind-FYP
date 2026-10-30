"""Single-writer advisory locks."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import psycopg

from wikimind.db.connection import connect


def _lock_key(project_id: UUID | str) -> str:
    """Canonicalize project IDs so object and string callers share a lock."""

    try:
        return str(UUID(str(project_id)))
    except ValueError:
        return str(project_id)


@contextmanager
def with_writer_lock(
    project_id: UUID | str,
    conn: psycopg.Connection | None = None,
) -> Iterator[psycopg.Connection]:
    """Hold the per-project writer lock for the duration of a transaction.

    The lock is a transaction-scoped advisory lock, so it is released by
    commit or rollback.  Passing a connection lets a caller combine the lock
    with its own writes in one transaction.

    The key derivation below is canonical (docs/contracts/schema.md,
    "Writer lock"): all lanes must take this lock through this helper, never
    with raw advisory-lock SQL.
    """

    owns_connection = conn is None
    active = conn or connect()
    try:
        with active.transaction():
            active.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s::text, 0))",
                (_lock_key(project_id),),
            )
            yield active
    finally:
        if owns_connection:
            active.close()


def writer_lock_held(conn: psycopg.Connection, project_id: UUID | str) -> bool:
    """Whether ``conn`` currently holds a lock on the project writer key.

    Checks ``pg_locks`` for a granted advisory lock on the same canonical key
    :func:`with_writer_lock` takes.  Postgres stores a bigint advisory key
    split across ``classid`` (high 32 bits) and ``objid`` (low 32 bits, typed
    ``oid``), so the two halves are compared separately; both are read back
    through ``::bigint`` since ``objid`` cannot hold the full 64-bit hash.
    ``objsubid = 1`` pins the single-int8-key form against the two-int4 form.

    Caveat: the check reports any lock on that key, transaction- or
    session-scoped.  Today :func:`with_writer_lock` (transaction-scoped) is
    the only taker.  Read-only; usable on any connection.
    """

    row = conn.execute(
        """
        SELECT 1
        FROM pg_locks
        WHERE locktype = 'advisory'
          AND pid = pg_backend_pid()
          AND granted
          AND objsubid = 1
          AND classid::bigint = ((hashtextextended(%s::text, 0) >> 32) & 4294967295)
          AND objid::bigint = (hashtextextended(%s::text, 0) & 4294967295)
        """,
        (_lock_key(project_id), _lock_key(project_id)),
    ).fetchone()
    return row is not None
