"""Ordered SQL migration runner with recorded SHA-256 hashes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from wikimind.config import redact
from wikimind.db.connection import connect

MIGRATION_NAME = re.compile(r"^\d{3}_[A-Za-z0-9_.-]+\.sql$")
MIGRATIONS_TABLE = "schema_migrations"


class MigrationError(RuntimeError):
    """A migration could not be applied safely."""

    exit_code = 2


class MigrationHashMismatch(MigrationError):
    """An already-applied migration's file hash changed."""


@dataclass(frozen=True, slots=True)
class Migration:
    path: Path
    name: str
    sha256: str


def default_migrations_dir() -> Path:
    """Find the repository's ``migrations/`` directory.

    Editable installs keep the package under ``src/``, so walk parents before
    falling back to the current working directory.
    """

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "migrations"
        if candidate.is_dir():
            return candidate
    return Path.cwd() / "migrations"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_migrations(migrations_dir: str | Path | None = None) -> list[Migration]:
    """Return migration files in filename order."""

    directory = Path(migrations_dir) if migrations_dir is not None else default_migrations_dir()
    if not directory.is_dir():
        raise MigrationError(f"migrations directory not found: {directory}")

    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql"), key=lambda item: item.name):
        if not MIGRATION_NAME.fullmatch(path.name):
            raise MigrationError(f"invalid migration filename {path.name!r}; expected NNN_name.sql")
        migrations.append(Migration(path=path, name=path.name, sha256=sha256_file(path)))

    if not migrations:
        raise MigrationError(f"no migrations found in {directory}")
    return migrations


def ensure_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} (
            name TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def applied_migrations(conn: psycopg.Connection) -> dict[str, str]:
    """Return ``{name: sha256}`` for applied migrations."""

    rows = conn.execute(f"SELECT name, sha256 FROM {MIGRATIONS_TABLE}").fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def run_migrations(
    dsn: str | None = None,
    migrations_dir: str | Path | None = None,
) -> list[str]:
    """Apply pending migrations in filename order.

    Each SQL file and its bookkeeping row commit in one transaction.  Re-running
    verifies every recorded hash and raises :class:`MigrationHashMismatch` on a
    mismatch.  Returns the names of newly applied migrations.
    """

    migrations = discover_migrations(migrations_dir)
    conn = connect(dsn, autocommit=True)
    applied: list[str] = []
    try:
        ensure_migrations_table(conn)
        recorded = applied_migrations(conn)
        known = {migration.name for migration in migrations}
        missing = sorted(set(recorded) - known)
        if missing:
            raise MigrationError("applied migration file(s) missing: " + ", ".join(missing))

        for migration in migrations:
            existing_hash = recorded.get(migration.name)
            if existing_hash is not None:
                if existing_hash != migration.sha256:
                    raise MigrationHashMismatch(
                        f"migration hash mismatch for {migration.name}: "
                        f"recorded {existing_hash}, file {migration.sha256}"
                    )
                continue

            sql = migration.path.read_text(encoding="utf-8")
            try:
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        f"INSERT INTO {MIGRATIONS_TABLE} (name, sha256) VALUES (%s, %s)",
                        (migration.name, migration.sha256),
                    )
            except psycopg.Error as exc:
                raise MigrationError(f"failed to apply {migration.name}: {redact(exc)}") from exc
            applied.append(migration.name)

        return applied
    finally:
        conn.close()


def verify_migration_hashes(
    conn: psycopg.Connection, migrations_dir: str | Path | None = None
) -> list[str]:
    """Return human-readable migration integrity problems.

    Empty list means the recorded files and hashes agree.
    """

    problems: list[str] = []
    try:
        migrations = discover_migrations(migrations_dir)
    except MigrationError as exc:
        return [str(exc)]

    try:
        recorded = applied_migrations(conn)
    except psycopg.Error as exc:
        return [f"cannot read {MIGRATIONS_TABLE}: {redact(exc)}"]

    files = {migration.name: migration for migration in migrations}
    for name, migration in files.items():
        if name not in recorded:
            problems.append(f"migration not applied: {name}")
            continue
        if recorded[name] != migration.sha256:
            problems.append(
                f"migration hash mismatch for {name}: "
                f"recorded {recorded[name]}, file {migration.sha256}"
            )
    for name in sorted(recorded.keys() - files.keys()):
        problems.append(f"applied migration has no file: {name}")
    return problems
