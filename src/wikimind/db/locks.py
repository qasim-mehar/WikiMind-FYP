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
