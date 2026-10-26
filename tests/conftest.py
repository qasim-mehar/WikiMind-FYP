"""Shared DB fixtures.

DB tests require ``WIKIMIND_DB`` in the process environment.  The file is
deliberately not read here: running pytest without an explicit DSN must skip
the database suite.  The configured role needs CREATEDB.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from wikimind.db.connection import with_dbname
from wikimind.db.migrations import run_migrations


def _connect_admin(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, connect_timeout=5)


def _drop_database(base_dsn: str, name: str) -> None:
    with _connect_admin(base_dsn) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def _create_database(base_dsn: str, name: str) -> None:
    with _connect_admin(base_dsn) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture(scope="session")
def base_dsn() -> str:
    dsn = os.environ.get("WIKIMIND_DB")
    if not dsn:
        pytest.skip("WIKIMIND_DB is not set; DB tests skipped")
    try:
        with psycopg.connect(dsn, connect_timeout=5):
            pass
    except (psycopg.OperationalError, psycopg.InterfaceError, ValueError, OSError):
        pytest.skip("WIKIMIND_DB is not reachable; DB tests skipped")
    return dsn


@pytest.fixture(scope="session")
def test_dsn(base_dsn: str) -> Iterator[str]:
    name = f"wikimind_test_{os.getpid()}"
    _drop_database(base_dsn, name)
    _create_database(base_dsn, name)
    dsn = with_dbname(base_dsn, name)
    try:
        run_migrations(dsn)
        yield dsn
    finally:
        _drop_database(base_dsn, name)


@pytest.fixture
def empty_db_dsn(base_dsn: str) -> Iterator[str]:
    name = f"wikimind_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    _create_database(base_dsn, name)
    dsn = with_dbname(base_dsn, name)
    try:
        yield dsn
    finally:
        _drop_database(base_dsn, name)


@pytest.fixture
def db_conn(test_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(test_dsn) as conn:
        yield conn
