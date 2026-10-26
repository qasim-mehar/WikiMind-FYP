"""Destructive database reset helper."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

from wikimind.db.connection import connect, database_name, resolve_dsn, with_dbname
from wikimind.db.migrations import MigrationError, run_migrations

TEMPLATE_DATABASE = "wikimind_template"
PROTECTED_DATABASES = ("postgres", TEMPLATE_DATABASE)


def template_exists(conn: psycopg.Connection) -> bool:
    """Return True when the local pgvector template database exists."""

    row = conn.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s AND datistemplate",
        (TEMPLATE_DATABASE,),
    ).fetchone()
    return row is not None


def reset_database(
    dsn: str | None = None,
    migrations_dir: str | Path | None = None,
) -> list[str]:
    """Rebuild the target database and re-apply all migrations.

    When the ``wikimind_template`` pgvector template exists (local
    non-superuser setup), the target database is dropped and recreated with
    ``WITH TEMPLATE`` so the ``vector`` extension is present without
    superuser rights.  Where the template does not exist (CI service
    containers with a superuser role), the public schema is dropped and
    recreated in place instead.
    """

    resolved = resolve_dsn(dsn)
    target = database_name(resolved)
    if target in PROTECTED_DATABASES:
        raise MigrationError(f"refusing to reset protected database {target!r}")

    admin_dsn = with_dbname(resolved, "postgres")
    try:
        conn = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError:
        conn = None
    if conn is not None:
        try:
            if template_exists(conn):
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(target)
                    )
                )
                conn.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                        sql.Identifier(target), sql.Identifier(TEMPLATE_DATABASE)
                    )
                )
                return run_migrations(dsn, migrations_dir)
        finally:
            conn.close()

    with connect(dsn, autocommit=True) as conn, conn.transaction():
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    return run_migrations(dsn, migrations_dir)
