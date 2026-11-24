"""CLI: ``wikimind integrity <command>``.

None of these commands issues an LLM call.  The root app in ``wikimind.cli``
mounts this sub-app; ``python -m wikimind.integrity.cli`` also works.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import psycopg
import typer
from psycopg.rows import dict_row

from wikimind.config import get_settings, redact
from wikimind.db.connection import connect

from .answers import AnswerDocument, load_answer_document, verify_answer
from .checks import check_provenance
from .contradictions import list_contradictions
from .costs import import_costs
from .ledger import verify_ledger
from .model import IntegrityReport
from .removal import MODE_TOMBSTONE, remove_document, scan_leaks

app = typer.Typer(
    name="integrity",
    help="Provenance checks, audit writers and ledger verification (no LLM calls).",
)

DsnOption = Annotated[
    str | None,
    typer.Option("--dsn", help="Postgres DSN; defaults to WIKIMIND_DB."),
]
JsonOption = Annotated[
    bool,
    typer.Option("--json", help="Emit the machine-readable summary as JSON."),
]
ProjectOption = Annotated[
    str | None,
    typer.Option("--project", help="Project name or UUID; defaults to the claim's project."),
]


def _connect(dsn: str | None) -> psycopg.Connection:
    resolved = dsn or get_settings().db_dsn
    if not resolved:
        typer.echo("error: no database DSN configured; set WIKIMIND_DB or pass --dsn", err=True)
        raise typer.Exit(2)
    return connect(resolved, row_factory=dict_row, autocommit=True)


def _project_id(conn: psycopg.Connection, ref: str) -> UUID:
    from wikimind.compile.errors import CompileError
    from wikimind.compile.graph import resolve_project

    try:
        return resolve_project(conn, ref).id
    except CompileError as exc:
        typer.echo(f"error: {redact(str(exc))}", err=True)
        raise typer.Exit(2) from exc


def _finish(report: IntegrityReport, as_json: bool, *, show_rows: bool = False) -> None:
    if as_json:
        typer.echo(json.dumps(report.to_dict(), sort_keys=True, default=str))
    else:
        typer.echo(report.render())
        if show_rows:
            for row in report.rows:
                typer.echo(
                    "  contradiction {edge_id}: claim={claim_id} document={document_sha256} "
                    "nodes={src_node}->{dst_node} unit={unit_channel_id}/{unit_compile_version}".format(
                        **row
                    )
                )
    raise typer.Exit(report.exit_code)


def _db_error(exc: psycopg.Error) -> None:
    typer.echo(f"error: {redact(str(exc))}", err=True)
    raise typer.Exit(2)


@app.command("check")
def check(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    dsn: DsnOption = None,
    as_json: JsonOption = False,
) -> None:
    """Walk the project's graph and report provenance/version findings."""

    conn = _connect(dsn)
    try:
        project_id = _project_id(conn, project)
        report = check_provenance(conn, project_id)
    except psycopg.Error as exc:
        _db_error(exc)
    finally:
        conn.close()
    _finish(report, as_json)


@app.command("import-costs")
def import_cost_command(
    file: Annotated[Path, typer.Argument(help="Historical costs.jsonl to import.")],
    project: ProjectOption = None,
    dsn: DsnOption = None,
    as_json: JsonOption = False,
) -> None:
    """Backfill a costs.jsonl into cost_events; re-running imports nothing."""

    conn = _connect(dsn)
    try:
        project_id = _project_id(conn, project) if project else None
        report = import_costs(file, conn=conn, project_id=project_id)
    except psycopg.Error as exc:
        _db_error(exc)
    finally:
        conn.close()
    _finish(report, as_json)


@app.command("verify-answer")
def verify_answer_command(
    file: Annotated[Path, typer.Argument(help="Answer JSON file to verify.")],
    project: ProjectOption = None,
    dsn: DsnOption = None,
    as_json: JsonOption = False,
) -> None:
    """Verify a QA answer's claim ids and document_slug citations."""

    try:
        answer: AnswerDocument = load_answer_document(file)
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        report = IntegrityReport("answer")
        report.finding(str(exc))
        _finish(report, as_json)

    conn = _connect(dsn)
    try:
        project_id = _project_id(conn, project) if project else None
        report = verify_answer(conn, answer, project_id=project_id)
    except psycopg.Error as exc:
        _db_error(exc)
    finally:
        conn.close()
    _finish(report, as_json)


@app.command("contradictions")
def contradictions_command(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    dsn: DsnOption = None,
    as_json: JsonOption = False,
) -> None:
    """List recorded contradicts dispositions (read-only)."""

    conn = _connect(dsn)
    try:
        project_id = _project_id(conn, project)
        report = list_contradictions(conn, project_id)
    except psycopg.Error as exc:
        _db_error(exc)
    finally:
        conn.close()
    _finish(report, as_json, show_rows=not as_json)


@app.command("remove")
def remove_command(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    target: Annotated[
        str,
        typer.Argument(
            help="Document sha256 (or prefix), source URI, filename stem, or citation slug."
        ),
    ],
    mode: Annotated[
        str,
        typer.Option(
            "--mode", "-m", help="Removal mode: tombstone (default), retracted, duplicate."
        ),
    ] = MODE_TOMBSTONE,
    dsn: DsnOption = None,
    as_json: JsonOption = False,
) -> None:
    """Tombstone, retract, or safely remove a duplicate document (no LLM calls)."""

    conn = _connect(dsn)
    try:
        project_id = _project_id(conn, project)
        report = remove_document(conn, project_id, target, mode)
    except psycopg.Error as exc:
        _db_error(exc)
    finally:
        conn.close()
    _finish(report, as_json)


@app.command("leaks")
def leaks_command(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    dsn: DsnOption = None,
    as_json: JsonOption = False,
) -> None:
    """Read-only leak scan: active pages still citing removed documents."""

    conn = _connect(dsn)
    try:
        project_id = _project_id(conn, project)
        report = scan_leaks(conn, project_id, flag_retracted=True)
    except psycopg.Error as exc:
        _db_error(exc)
    finally:
        conn.close()
    _finish(report, as_json)


@app.command("verify-ledger")
def verify_ledger_command(
    project: ProjectOption = None,
    dsn: DsnOption = None,
    as_json: JsonOption = False,
) -> None:
    """Verify every ledger chain end to end (or one project's chain)."""

    conn = _connect(dsn)
    try:
        project_id = _project_id(conn, project) if project else None
        report = verify_ledger(conn, project_id)
    except psycopg.Error as exc:
        _db_error(exc)
    finally:
        conn.close()
    _finish(report, as_json)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
