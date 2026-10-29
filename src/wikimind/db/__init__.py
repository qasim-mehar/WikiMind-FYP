"""Postgres access, migrations, locks, and invariant checks."""

from wikimind.db.checks import CheckReport, check_database, run_checks
from wikimind.db.connection import (
    DatabaseNotConfigured,
    connect,
    connection,
    database_name,
    resolve_dsn,
    transaction,
    with_dbname,
)
from wikimind.db.locks import with_writer_lock, writer_lock_held
from wikimind.db.migrations import (
    MigrationError,
    MigrationHashMismatch,
    applied_migrations,
    discover_migrations,
    run_migrations,
    verify_migration_hashes,
)

__all__ = [
    "CheckReport",
    "DatabaseNotConfigured",
    "MigrationError",
    "MigrationHashMismatch",
    "applied_migrations",
    "check_database",
    "connect",
    "connection",
    "database_name",
    "discover_migrations",
    "resolve_dsn",
    "run_checks",
    "run_migrations",
    "transaction",
    "verify_migration_hashes",
    "with_dbname",
    "with_writer_lock",
    "writer_lock_held",
]
