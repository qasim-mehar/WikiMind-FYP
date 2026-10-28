"""Shared DB fixtures.

DB tests require ``WIKIMIND_DB`` in the process environment.  The file is
deliberately not read here: running pytest without an explicit DSN must skip
the database suite.  The configured role needs CREATEDB.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.errors import ObjectInUse

from wikimind.config import redact
from wikimind.db.connection import with_dbname
from wikimind.db.migrations import run_migrations
from wikimind.db.reset import TEMPLATE_DATABASE, template_exists


class RedactedDsn(str):
    """DSN string whose repr is masked.

    pytest renders a failing test's fixture arguments with ``repr``, so a
    DSN carrying a password would leak it into tracebacks (observed in
    Lane 3).  This subclass behaves exactly like ``str`` everywhere —
    ``psycopg.connect``, ``conninfo`` helpers, dict keys — but its repr goes
    through the redactor.  Belt: the fixtures below already strip the
    password from the DSN and hand it to libpq via ``PGPASSWORD``; the
    masked repr only matters if a password ever re-enters a DSN.
    """

    def __repr__(self) -> str:
        return repr(redact(str(self)))


def _dsn_without_password(dsn: str) -> str:
    """Return ``dsn`` with any password removed, moved to ``PGPASSWORD``.

    pytest's assertion diff prints raw string text (not reprs), so masking
    the repr is not enough: the password must not be in any string pytest
    can print at all.  libpq reads ``PGPASSWORD`` for every connection made
    through these fixtures, so behavior is unchanged.
    """

    params = conninfo_to_dict(dsn)
    password = params.pop("password", None)
    if password:
        os.environ["PGPASSWORD"] = password
    return make_conninfo(**params)


def _connect_admin(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True, connect_timeout=5)


def _drop_database(base_dsn: str, name: str) -> None:
    with _connect_admin(base_dsn) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid() "
            "AND backend_type = 'client backend'",
            (name,),
        )
        for attempt in range(3):
            try:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
                return
            except ObjectInUse:
                time.sleep(0.5 * (attempt + 1))
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def _create_database(base_dsn: str, name: str) -> None:
    """Create a throwaway database, from the pgvector template when present.

    The local role is not a superuser, so ``CREATE EXTENSION vector`` fails;
    locally every database is created ``WITH TEMPLATE wikimind_template``.
    CI service containers have no template but a superuser role, so a plain
    ``CREATE DATABASE`` plus the migration's ``CREATE EXTENSION IF NOT
    EXISTS`` covers them.
    """

    with _connect_admin(base_dsn) as conn:
        if template_exists(conn):
            conn.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                    sql.Identifier(name), sql.Identifier(TEMPLATE_DATABASE)
                )
            )
        else:
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
    return RedactedDsn(_dsn_without_password(dsn))


@pytest.fixture(scope="session")
def test_dsn(base_dsn: str) -> Iterator[str]:
    name = f"wikimind_test_{os.getpid()}"
    _drop_database(base_dsn, name)
    _create_database(base_dsn, name)
    dsn = RedactedDsn(with_dbname(base_dsn, name))
    try:
        run_migrations(dsn)
        yield dsn
    finally:
        _drop_database(base_dsn, name)


@pytest.fixture
def empty_db_dsn(base_dsn: str) -> Iterator[str]:
    name = f"wikimind_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    _create_database(base_dsn, name)
    dsn = RedactedDsn(with_dbname(base_dsn, name))
    try:
        yield dsn
    finally:
        _drop_database(base_dsn, name)


@pytest.fixture
def db_conn(test_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(test_dsn) as conn:
        yield conn
