"""CLI: ``wikimind compile <project>`` plus minimal ``project``/``document``
registration commands (C2).  The root app in ``wikimind.cli`` mounts
``compile_cmd`` as the top-level ``compile`` command and the two sub-apps.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from psycopg.rows import dict_row

from wikimind.config import get_settings, redact
from wikimind.db.connection import connect
from wikimind.llm.cost import CostMeter, JsonlCostSink
from wikimind.llm.errors import WikiMindLLMError
from wikimind.llm.gemini import GeminiClient
from wikimind.llm.judge import JudgeClient
from wikimind.llm.secrets import load_dotenv

from .errors import CompileError
from .graph import create_project, list_documents, list_projects, register_document, resolve_project
from .pipeline import COMPILE_JUDGE_MIN_SCORE, compile_project
from .stages import inter_call_delay

project_app = typer.Typer(help="Create and list projects.")
document_app = typer.Typer(help="Register source documents into a project.")

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
        typer.echo("error: no database DSN configured; set WIKIMIND_DB or pass --dsn", err=True)
        raise typer.Exit(2)
    # Autocommit: with_writer_lock manages explicit transactions; reads and
    # bookkeeping writes commit immediately.
    return connect(resolved, row_factory=dict_row, autocommit=True)


def run_compile_command(
    project_ref: str,
    dsn: str | None,
    costs_path: Path | None,
    delay: float | None,
    min_score: int,
) -> int:
    """Shared implementation; returns the process exit code."""

    if os.environ.get("WIKIMIND_LIVE") != "1":
        typer.echo(
            "error: live compile requires WIKIMIND_LIVE=1 (it issues real, "
            "metered LLM calls); keyless runs use the library API with fakes",
            err=True,
        )
        return 2

    try:
        conn = _connect(dsn)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {redact(exc)}", err=True)
        return 2

    resolved_delay = delay if delay is not None else inter_call_delay()
    meter = CostMeter(JsonlCostSink(costs_path))
    try:
        project = resolve_project(conn, project_ref)
        drafter = GeminiClient(meter=meter, temperature=0.0)
        judge = JudgeClient(meter=meter, temperature=0.0)
        result = compile_project(
            conn,
            project=project,
            drafter=drafter,
            judge=judge,
            min_score=min_score,
            delay=resolved_delay,
            meter=meter,
        )
    except (WikiMindLLMError, CompileError) as exc:
        typer.echo(f"error: {redact(str(exc))}", err=True)
        return 2 if isinstance(exc, CompileError) else 1
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: {redact(exc)}", err=True)
        return 1
    finally:
        conn.close()

    stats = result.stats
    typer.echo(f"project {result.project.name} ({result.project.id})")
    typer.echo(f"run {result.run_id} status={result.status}")
    typer.echo(
        f"documents: {stats.documents_seen} seen, {stats.documents_skipped} skipped "
        f"(unchanged); units: {stats.units_seen} seen, {stats.units_skipped} skipped"
    )
    typer.echo(
        f"claims: {stats.claims_drafted} drafted, {stats.dedup_filtered} deduped, "
        f"{stats.anchor_rejects} anchor-rejected, {stats.schema_rejects} schema-rejected, "
        f"{stats.claims_committed} committed"
    )
    typer.echo(
        f"judge: {stats.judge_accepted} accepted, {stats.judge_rejected} rejected "
        f"(min score {min_score}); disposition repairs: {stats.disposition_repairs}"
    )
    dispositions = " ".join(f"{name}={count}" for name, count in stats.dispositions.items())
    typer.echo(f"dispositions: {dispositions}")
    typer.echo(
        f"graph delta: nodes +{stats.nodes_created}, edges +{stats.edges_created}, "
        f"node versions +{stats.node_versions}"
    )
    typer.echo(f"run shadow cost: ${result.run_cost_usd:.12f}")
    return 0


def compile_cmd(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    dsn: DsnOption = None,
    costs_path: CostsPathOption = None,
    delay: Annotated[
        float | None,
        typer.Option(
            "--delay",
            help="Seconds to wait between LLM calls (rate-limit pacing; "
            "default: 0 or WIKIMIND_COMPILE_DELAY).",
        ),
    ] = None,
    min_score: Annotated[
        int,
        typer.Option(
            "--min-score",
            help=f"Minimum judge score to commit a claim (default {COMPILE_JUDGE_MIN_SCORE}).",
            min=0,
            max=5,
        ),
    ] = COMPILE_JUDGE_MIN_SCORE,
) -> None:
    """Compile every pending channel unit of every document in the project."""

    load_dotenv()
    raise typer.Exit(run_compile_command(project, dsn, costs_path, delay, min_score))


@project_app.command("create")
def project_create(
    name: Annotated[str, typer.Argument(help="Display name of the project.")],
    dsn: DsnOption = None,
) -> None:
    """Create a project and print its id."""

    load_dotenv()
    try:
        conn = _connect(dsn)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(2) from exc
    try:
        project = create_project(conn, name)
    except Exception as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(1) from exc
    finally:
        conn.close()
    typer.echo(f"created project {project.name} ({project.id})")


@project_app.command("list")
def project_list(dsn: DsnOption = None) -> None:
    """List projects."""

    load_dotenv()
    try:
        conn = _connect(dsn)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(2) from exc
    try:
        projects = list_projects(conn)
    finally:
        conn.close()
    if not projects:
        typer.echo("no projects")
        return
    for project in projects:
        typer.echo(f"{project.id}  {project.name}")


@document_app.command("add")
def document_add(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    files: Annotated[list[Path], typer.Argument(exists=True, readable=True)],
    dsn: DsnOption = None,
    parser: Annotated[
        str | None,
        typer.Option("--parser", "-p", help="Force a parser name (e.g. grobid)."),
    ] = None,
) -> None:
    """Parse source files and register them (documents + channels)."""

    from wikimind.parse import ParseError, parse_file

    load_dotenv()
    try:
        conn = _connect(dsn)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(2) from exc
    try:
        try:
            resolved_project = resolve_project(conn, project)
        except CompileError as exc:
            typer.echo(f"error: {redact(str(exc))}", err=True)
            raise typer.Exit(2) from exc
        for file in files:
            try:
                parsed = parse_file(file, parser=parser)
            except (ParseError, OSError) as exc:
                typer.echo(f"parse error: {redact(exc)}", err=True)
                raise typer.Exit(1) from exc
            document_id, created = register_document(
                conn, resolved_project.id, parsed, source_uri=str(file.resolve())
            )
            state = "registered" if created else "already registered"
            typer.echo(
                f"{state}: {parsed.source_filename} sha={parsed.source_sha256[:12]} "
                f"parser={parsed.parser} channels={len(parsed.channels)} "
                f"document={document_id}"
            )
    finally:
        conn.close()


@document_app.command("list")
def document_list(
    project: Annotated[str, typer.Argument(help="Project name or UUID.")],
    dsn: DsnOption = None,
) -> None:
    """List the documents registered in a project."""

    load_dotenv()
    try:
        conn = _connect(dsn)
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(2) from exc
    try:
        try:
            resolved_project = resolve_project(conn, project)
        except CompileError as exc:
            typer.echo(f"error: {redact(str(exc))}", err=True)
            raise typer.Exit(2) from exc
        documents = list_documents(conn, resolved_project.id)
    finally:
        conn.close()
    if not documents:
        typer.echo("no documents")
        return
    for document in documents:
        typer.echo(f"{document.id}  sha={document.sha256[:12]}  {document.source_uri}")
