"""``wikimind db`` command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from wikimind.config import get_settings, redact
from wikimind.db.checks import check_database
from wikimind.db.migrations import MigrationError, run_migrations
from wikimind.db.reset import reset_database

app = typer.Typer(
    name="db",
    help="Migrate, inspect, and reset the WikiMind Postgres schema.",
    no_args_is_help=True,
)

DsnOption = Annotated[
    str | None,
    typer.Option("--dsn", help="Postgres DSN; defaults to WIKIMIND_DB."),
]
MigrationsDirOption = Annotated[
    Path | None,
    typer.Option("--migrations-dir", help="Directory containing NNN_name.sql files."),
]
ConfirmOption = Annotated[
    bool,
    typer.Option(
        "--confirm",
        help="Required acknowledgement that the public schema will be destroyed.",
    ),
]


def _dsn_or_exit(dsn: str | None) -> str:
    resolved = dsn or get_settings().db_dsn
    if not resolved:
        typer.echo(
            "error: no database DSN configured; set WIKIMIND_DB or pass --dsn",
            err=True,
        )
        raise typer.Exit(2)
    return resolved


@app.command()
def migrate(
    dsn: DsnOption = None,
    migrations_dir: MigrationsDirOption = None,
) -> None:
    """Apply pending migrations in filename order."""

    resolved = _dsn_or_exit(dsn)
    try:
        applied = run_migrations(resolved, migrations_dir)
    except MigrationError as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(exc.exit_code) from exc
    except Exception as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(2) from exc

    if applied:
        for name in applied:
            typer.echo(f"applied {name}")
    else:
        typer.echo("database is up to date")


@app.command()
def check(
    dsn: DsnOption = None,
    migrations_dir: MigrationsDirOption = None,
) -> None:
    """Check schema, migration hashes, orphans, provenance, enums, and embedding sizes."""

    report = check_database(_dsn_or_exit(dsn), migrations_dir)
    if report.pgvector_version:
        typer.echo(f"pgvector {report.pgvector_version}")
    else:
        typer.echo("pgvector: not installed")
    for finding in report.findings:
        typer.echo(f"{finding.level:<7} {finding.code}: {finding.message}")

    if report.errors:
        typer.echo(f"failed: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    elif report.warnings:
        typer.echo(f"warning: {len(report.warnings)} warning(s)")
    else:
        typer.echo("ok: database invariants hold")
    raise typer.Exit(report.exit_code)


@app.command()
def reset(
    confirm: ConfirmOption = False,
    dsn: DsnOption = None,
    migrations_dir: MigrationsDirOption = None,
) -> None:
    """Drop the public schema, recreate it, and re-apply migrations."""

    if not confirm:
        typer.echo("refusing to reset without --confirm", err=True)
        raise typer.Exit(2)

    resolved = _dsn_or_exit(dsn)
    try:
        applied = reset_database(resolved, migrations_dir)
    except MigrationError as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(exc.exit_code) from exc
    except Exception as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(2) from exc

    for name in applied:
        typer.echo(f"applied {name}")
    typer.echo("database reset complete")
