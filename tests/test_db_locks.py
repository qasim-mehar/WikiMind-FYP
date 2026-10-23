from __future__ import annotations

import psycopg
import pytest

from wikimind.db.locks import with_writer_lock


def test_writer_lock_serializes_projects(test_dsn: str) -> None:
    with psycopg.connect(test_dsn) as setup:
        row = setup.execute(
            "INSERT INTO projects (name) VALUES ('lock-test') RETURNING id"
        ).fetchone()
        assert row is not None
        project_id = row[0]

    with psycopg.connect(test_dsn) as first, with_writer_lock(project_id, first):
        first.execute("SELECT 1")

        with psycopg.connect(test_dsn) as second:
            second.execute("SET lock_timeout = '200ms'")
            with (
                pytest.raises(psycopg.errors.LockNotAvailable),
                with_writer_lock(project_id, second),
            ):
                pass

    with psycopg.connect(test_dsn) as third, with_writer_lock(project_id, third):
        third.execute("SELECT 1")
