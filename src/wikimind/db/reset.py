"""Destructive database reset helper."""

from __future__ import annotations

from pathlib import Path

from wikimind.db.connection import connect
from wikimind.db.migrations import run_migrations


def reset_database(
    dsn: str | None = None,
    migrations_dir: str | Path | None = None,
) -> list[str]:
    """Drop the public schema, recreate it, then re-apply all migrations."""

    conn = connect(dsn, autocommit=True)
    try:
        with conn.transaction():
            conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
            conn.execute("CREATE SCHEMA public")
            conn.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    finally:
        conn.close()
    return run_migrations(dsn, migrations_dir)
