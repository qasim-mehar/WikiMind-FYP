from __future__ import annotations

import shutil
from pathlib import Path

import psycopg
from typer.testing import CliRunner

from wikimind.cli import app

runner = CliRunner()
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_root_cli_mounts_db() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "db" in result.output


def test_db_migrate_and_check(empty_db_dsn: str) -> None:
    migrated = runner.invoke(app, ["db", "migrate", "--dsn", empty_db_dsn])
    assert migrated.exit_code == 0
    assert "applied 000_enable_vector.sql" in migrated.output
    assert "applied 001_schema_v1.sql" in migrated.output

    checked = runner.invoke(app, ["db", "check", "--dsn", empty_db_dsn])
    assert checked.exit_code == 0
    assert "ok: database invariants hold" in checked.output

    with psycopg.connect(empty_db_dsn) as conn:
        assert conn.execute("SELECT to_regclass('public.embeddings')").fetchone() == ("embeddings",)


def test_reset_requires_confirmation(empty_db_dsn: str) -> None:
    result = runner.invoke(app, ["db", "reset", "--dsn", empty_db_dsn])
    assert result.exit_code == 2
    assert "without --confirm" in result.output


def test_reset_rebuilds_schema(empty_db_dsn: str) -> None:
    assert runner.invoke(app, ["db", "migrate", "--dsn", empty_db_dsn]).exit_code == 0
    result = runner.invoke(app, ["db", "reset", "--confirm", "--dsn", empty_db_dsn])
    assert result.exit_code == 0
    assert "database reset complete" in result.output
    assert runner.invoke(app, ["db", "check", "--dsn", empty_db_dsn]).exit_code == 0


def test_check_connection_failure_exits_two() -> None:
    result = runner.invoke(
        app,
        [
            "db",
            "check",
            "--dsn",
            "postgresql://nobody@127.0.0.1:1/nope?connect_timeout=1",
        ],
    )
    assert result.exit_code == 2
    assert "db.connect" in result.output


def test_migrate_hash_mismatch_exits_two(empty_db_dsn: str, tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for source in sorted(MIGRATIONS.glob("*.sql")):
        shutil.copy2(source, migrations / source.name)

    first = runner.invoke(
        app,
        [
            "db",
            "migrate",
            "--dsn",
            empty_db_dsn,
            "--migrations-dir",
            str(migrations),
        ],
    )
    assert first.exit_code == 0

    changed = migrations / "001_schema_v1.sql"
    changed.write_text(changed.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")
    second = runner.invoke(
        app,
        [
            "db",
            "migrate",
            "--dsn",
            empty_db_dsn,
            "--migrations-dir",
            str(migrations),
        ],
    )
    assert second.exit_code == 2
    assert "hash mismatch" in second.output
