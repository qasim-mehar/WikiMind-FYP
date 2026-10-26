from __future__ import annotations

import uuid

import psycopg
import pytest

from wikimind.db.locks import with_writer_lock


def test_writer_lock_serializes_projects(test_dsn: str) -> None:
    project_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    with psycopg.connect(test_dsn) as setup:
        setup.execute(
            "INSERT INTO projects (id, name) VALUES (%s, 'lock-test')",
            (project_id,),
        )

    with psycopg.connect(test_dsn) as first, with_writer_lock(project_id, first):
        first.execute("SELECT 1")

        with psycopg.connect(test_dsn) as second:
            second.execute("SET lock_timeout = '200ms'")
            with (
                pytest.raises(psycopg.errors.LockNotAvailable),
                with_writer_lock(str(project_id).upper(), second),
            ):
                pass

    with psycopg.connect(test_dsn) as third, with_writer_lock(project_id, third):
        third.execute("SELECT 1")
