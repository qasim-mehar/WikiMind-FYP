"""CLI: ``wikimind render backfill <project>``.

Materializes node versions for nodes that compile-v0 committed without any
(the pre-Ruling-14 corpus).  Purely local: rendering is a deterministic
function of stored rows and issues no LLM calls, so there is no call site
and no cost-ledger entry.  The root app in ``wikimind.cli`` mounts this as
the ``render`` sub-app.
"""

from __future__ import annotations

from typing import Annotated

import psycopg
import typer
from psycopg.rows import dict_row

from wikimind.config import get_settings, redact
from wikimind.db.connection import connect

from .versions import backfill_project

app = typer.Typer(help="Materialize node versions from stored rows.")

DsnOption = Annotated[
    str | None,
    typer.Option("--dsn", help="Postgres DSN; defaults to WIKIMIND_DB."),
]


def _resolve_project_id(conn: psycopg.Connection, ref: str):
    from ..compile.errors import CompileError
    from ..compile.graph import resolve_project

    try:
        return resolve_project(conn, ref).id
    except CompileError as exc:
        typer.echo(f"error: {redact(str(exc))}", err=True)
        raise typer.Exit(2) from exc


@app.command("backfill")
def backfill(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    dsn: DsnOption = None,
) -> None:
    """Version every node of the project that has no node_versions row.

    Idempotent: a second run writes nothing.  Nodes that already have
    versions are left untouched, except that a node with versions but a NULL
    current_version_id is repaired to point at its latest version.  Until
    repaired, versioned-but-NULL nodes are invisible to retrieval: indexing
    and ANN/full-text search scope to current versions only.
    """

    resolved = dsn or get_settings().db_dsn
    if not resolved:
        typer.echo("error: no database DSN configured; set WIKIMIND_DB or pass --dsn", err=True)
        raise typer.Exit(2)

    # Autocommit: with_writer_lock manages the transaction; reads commit
    # immediately.
    conn = connect(resolved, row_factory=dict_row, autocommit=True)
    try:
        project_id = _resolve_project_id(conn, project)
        try:
            result = backfill_project(conn, project_id)
        except psycopg.Error as exc:
            typer.echo(f"error: {redact(exc)}", err=True)
            raise typer.Exit(2) from exc
    finally:
        conn.close()

    typer.echo(
        f"nodes: {result.nodes_total} total, {result.versioned} versioned, "
        f"{result.already_versioned} already versioned, {result.repaired} repaired"
    )


def main() -> None:
    """Run the render command standalone."""
    typer.run(backfill)


if __name__ == "__main__":
    main()
