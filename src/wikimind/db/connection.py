"""Postgres connection and transaction helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from wikimind.config import get_settings


class DatabaseNotConfigured(RuntimeError):
    """Raised when no database DSN is available."""


def resolve_dsn(dsn: str | None = None) -> str:
    """Return an explicit DSN, the environment DSN, or raise."""

    if dsn:
        return dsn
    configured = get_settings().db_dsn
    if configured:
        return configured
    raise DatabaseNotConfigured("No database DSN configured. Set WIKIMIND_DB or pass --dsn.")


def connect(
    dsn: str | None = None,
    *,
    autocommit: bool = False,
    row_factory: Any = None,
    **kwargs: Any,
) -> psycopg.Connection:
    """Open a Postgres connection using the configured DSN."""

    conn = psycopg.connect(
        resolve_dsn(dsn),
        autocommit=autocommit,
        row_factory=row_factory,
        **kwargs,
    )
    return conn


@contextmanager
def transaction(dsn: str | None = None) -> Iterator[psycopg.Connection]:
    """Open a connection and run a block in one transaction."""

    conn = connect(dsn)
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.close()


@contextmanager
def connection(dsn: str | None = None) -> Iterator[psycopg.Connection]:
    """Open and close a connection without forcing a transaction."""

    conn = connect(dsn)
    try:
        yield conn
    finally:
        conn.close()


def database_name(dsn: str | None = None) -> str:
    """Return the database name from a DSN, without exposing credentials."""

    params = conninfo_to_dict(resolve_dsn(dsn))
    return str(params.get("dbname", ""))


def with_dbname(dsn: str, database: str) -> str:
    """Return ``dsn`` pointed at a different database."""

    params = conninfo_to_dict(dsn)
    params["dbname"] = database
    return make_conninfo(**params)
