from __future__ import annotations

import uuid

import psycopg
import pytest

from wikimind.db.locks import with_writer_lock, writer_lock_held


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


def test_writer_lock_held_reports_only_the_holding_connection(test_dsn: str) -> None:
    project_id = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    other_id = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    with psycopg.connect(test_dsn) as setup:
        setup.execute(
            "INSERT INTO projects (id, name) VALUES (%s, 'lock-held-a'), (%s, 'lock-held-b')",
            (project_id, other_id),
        )

    holder = psycopg.connect(test_dsn)
    try:
        with with_writer_lock(project_id, holder):
            assert writer_lock_held(holder, project_id)
            # A different project's key is not held on this connection.
            assert not writer_lock_held(holder, other_id)
            # The key derivation matches with_writer_lock's (string form included).
            assert writer_lock_held(holder, str(project_id).upper())

        # Transaction-scoped: released by the commit that exits the context,
        # while the connection itself stays open.
        assert not writer_lock_held(holder, project_id)
    finally:
        holder.close()

    with psycopg.connect(test_dsn) as bystander:
        assert not writer_lock_held(bystander, project_id)
