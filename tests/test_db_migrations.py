from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from wikimind.db.checks import check_database
from wikimind.db.migrations import (
    MigrationError,
    MigrationHashMismatch,
    discover_migrations,
    run_migrations,
)

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _copy_migrations(target: Path) -> None:
    target.mkdir()
    for source in sorted(MIGRATIONS.glob("*.sql")):
        shutil.copy2(source, target / source.name)


def test_migrations_apply_once(empty_db_dsn: str) -> None:
    applied = run_migrations(empty_db_dsn)
    assert applied == ["000_enable_vector.sql", "001_schema_v1.sql"]
    assert run_migrations(empty_db_dsn) == []


def test_late_lower_numbered_migration_is_rejected(empty_db_dsn: str, tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    _copy_migrations(migrations)
    run_migrations(empty_db_dsn, migrations)

    (migrations / "000_extra.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationError, match="sorts before already applied"):
        run_migrations(empty_db_dsn, migrations)


def test_hash_mismatch_is_an_error(empty_db_dsn: str, tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    _copy_migrations(migrations)
    run_migrations(empty_db_dsn, migrations)

    changed = migrations / "001_schema_v1.sql"
    changed.write_text(changed.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")

    with pytest.raises(MigrationHashMismatch) as exc_info:
        run_migrations(empty_db_dsn, migrations)
    assert exc_info.value.exit_code == 2

    report = check_database(empty_db_dsn, migrations)
    assert report.exit_code == 2
    assert any(finding.code == "migrations.integrity" for finding in report.findings)


def test_invalid_migration_filename_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "bad.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        discover_migrations(tmp_path)


def test_clean_database_passes_checks(test_dsn: str) -> None:
    report = check_database(test_dsn)
    assert report.exit_code == 0
    assert report.findings == []
