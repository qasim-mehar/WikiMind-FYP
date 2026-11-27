"""CLI for the friendly ingestion flow: ``wikimind add``.

One command replaces the old three-step manual dance (``project create`` →
``document add`` → ``compile``).  The root app mounts ``add_cmd`` as a
top-level command in ``wikimind.cli``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from psycopg.rows import dict_row

from ..compile.pipeline import COMPILE_JUDGE_MIN_SCORE
from ..config import get_settings, redact
from ..db.connection import connect
from ..llm.secrets import load_dotenv
from .errors import IngestConfigError, IngestError
from .estimate import DEFAULT_CLAIMS_PER_UNIT, CostEstimate
from .flow import add_documents

DsnOption = Annotated[
    str | None,
    typer.Option("--dsn", help="Postgres DSN; defaults to WIKIMIND_DB."),
]
CostsPathOption = Annotated[
    Path | None,
    typer.Option("--costs-path", help="JSONL cost ledger path (default: costs.jsonl)."),
]


def _connect(dsn: str | None):
    resolved = dsn or get_settings().db_dsn
    if not resolved:
        raise IngestConfigError(
            "no database DSN configured; set WIKIMIND_DB or pass --dsn",
            hint="copy .env.example to .env and fill in WIKIMIND_DB",
        )
    return connect(resolved, row_factory=dict_row, autocommit=True)


def _make_confirm(yes: bool):
    if yes:
        return lambda _estimate: True

    def confirm(estimate: CostEstimate) -> bool:
        return typer.confirm(
            f"Proceed with compile (expected shadow cost <= ${estimate.shadow_cost_usd:.12f})?",
            default=False,
        )

    return confirm


def add_cmd(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    files: Annotated[list[Path], typer.Argument(help="One or more source files to add.")],
    dsn: DsnOption = None,
    costs_path: CostsPathOption = None,
    parser: Annotated[
        str | None,
        typer.Option("--parser", "-p", help="Force a parser name (e.g. grobid, text)."),
    ] = None,
    delay: Annotated[
        float | None,
        typer.Option(
            "--delay",
            help="Seconds between LLM calls (rate-limit pacing; default: WIKIMIND_COMPILE_DELAY).",
        ),
    ] = None,
    min_score: Annotated[
        int,
        typer.Option(
            "--min-score",
            min=0,
            max=5,
            help=f"Minimum judge score to commit a claim (default {COMPILE_JUDGE_MIN_SCORE}).",
        ),
    ] = COMPILE_JUDGE_MIN_SCORE,
    claims_per_unit: Annotated[
        int,
        typer.Option(
            "--claims-per-unit",
            min=0,
            help="Cost-estimate assumption for claims per unit (default 3).",
        ),
    ] = DEFAULT_CLAIMS_PER_UNIT,
    force: Annotated[
        bool,
        typer.Option("--force", help="Recompile the added documents even if already compiled."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm the cost estimate non-interactively."),
    ] = False,
    no_compile: Annotated[
        bool,
        typer.Option("--no-compile", help="Only extract and register; make no LLM calls."),
    ] = False,
    create_project: Annotated[
        bool,
        typer.Option("--create-project", help="Create the project if it does not exist."),
    ] = False,
) -> None:
    """Extract, register and compile source documents in one visible flow."""

    load_dotenv()
    try:
        conn = _connect(dsn)
    except IngestError as exc:
        typer.echo(f"error: {exc}", err=True)
        if exc.hint:
            typer.echo(f"hint: {exc.hint}", err=True)
        raise typer.Exit(exc.exit_code) from exc
    except Exception as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(2) from exc

    try:
        try:
            outcome = add_documents(
                conn,
                project_ref=project,
                files=files,
                parser=parser,
                dsn=dsn,
                costs_path=costs_path,
                delay=delay,
                min_score=min_score,
                force=force,
                create_project=create_project,
                do_compile=not no_compile,
                claims_per_unit=claims_per_unit,
                confirm=_make_confirm(yes),
                progress=typer.echo,
            )
        except IngestError as exc:
            typer.echo(f"error: {exc}", err=True)
            if exc.hint:
                typer.echo(f"hint: {exc.hint}", err=True)
            raise typer.Exit(exc.exit_code) from exc
        except Exception as exc:
            typer.echo(f"error: {redact(exc)}", err=True)
            raise typer.Exit(1) from exc
    finally:
        conn.close()

    typer.echo(
        f"done: project {outcome.project_name} - "
        f"{outcome.new_documents} registered, "
        f"{outcome.already_registered} already registered"
    )
    if outcome.skipped_reason:
        typer.echo(f"skipped: {outcome.skipped_reason}")
        return
    if outcome.run is not None:
        stats = outcome.run.stats
        typer.echo(
            f"compile: units +{stats.units_seen - stats.units_skipped}, "
            f"claims +{stats.claims_committed}, edges +{stats.edges_created}, "
            f"versions +{stats.node_versions}"
        )
    typer.echo(f"shadow cost: ${outcome.shadow_cost_usd:.12f}")


__all__ = ["add_cmd"]
