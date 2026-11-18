"""CLI: ``wikimind ask "<question>"`` (terminal-only demo surface).

The root app in ``wikimind.cli`` mounts :func:`ask` as a top-level command,
alongside ``compile`` and ``index``. There is no web surface in Stage 1.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from psycopg import Error as PsycopgError
from psycopg.rows import dict_row

from wikimind.config import get_settings, redact
from wikimind.db.connection import connect
from wikimind.llm.cost import CostMeter, JsonlCostSink, format_usd
from wikimind.llm.embedder import SentenceTransformerEmbedder
from wikimind.llm.errors import WikiMindLLMError
from wikimind.llm.gemini import GeminiClient
from wikimind.llm.secrets import load_dotenv

from .errors import QAError
from .pipeline import (
    DEFAULT_CLAIM_CAP,
    DEFAULT_HOPS,
    DEFAULT_SEED_K,
    QA_BASE_DELAY,
    QA_MAX_ATTEMPTS,
    QA_TIMEOUT_S,
    AnswerResult,
    answer_question,
)
from .prompts import CALL_SITE_EMBED

DEFAULT_PROJECT = "v0-fixtures"
DsnOption = Annotated[
    str | None,
    typer.Option("--dsn", help="Postgres DSN; defaults to WIKIMIND_DB."),
]
CostsPathOption = Annotated[
    Path | None,
    typer.Option("--costs-path", help="JSONL cost ledger path (default: costs.jsonl)."),
]
ProjectOption = Annotated[
    str | None,
    typer.Option(
        "--project",
        "-p",
        help=f"Project to query (default: WIKIMIND_QA_PROJECT or {DEFAULT_PROJECT!r}).",
    ),
]


def _fail(message: object, code: int = 1) -> None:
    typer.echo(f"error: {redact(str(message))}", err=True)
    raise typer.Exit(code)


def _resolve_project_ref(conn, ref: str):
    from ..compile.errors import CompileError
    from ..compile.graph import resolve_project

    try:
        return resolve_project(conn, ref)
    except CompileError as exc:
        _fail(str(exc), 2)


def _connect(dsn: str | None):
    resolved = dsn or get_settings().db_dsn
    if not resolved:
        _fail("no database DSN configured; set WIKIMIND_DB or pass --dsn", 2)
    return connect(resolved, row_factory=dict_row, autocommit=True)


def _print_result(result: AnswerResult, question: str) -> None:
    typer.echo(f"question: {question}")
    typer.echo(f"answer:   {result.answer}")
    typer.echo(f"sufficient: {str(result.sufficient).lower()}")
    typer.echo("citations:")
    by_ref = {claim.ref: claim for claim in result.claims}
    if not result.citations:
        typer.echo("  (none)")
    for citation in result.citations:
        claim = by_ref.get(citation.claim_id)
        suffix = f" — {claim.text}" if claim is not None else ""
        typer.echo(f"  [{citation.claim_id}] {citation.document_slug}{suffix}")
    retrieval = (
        f"retrieval: {len(result.seeds)} seeds, {len(result.claims)}/{result.claim_cap} claims"
    )
    if result.truncated:
        retrieval += " (capped)"
    typer.echo(retrieval)
    typer.echo(
        f"shadow:   {format_usd(result.shadow_cost_usd)} ({result.ledger_events} ledger events)"
    )
    typer.echo(f"wall:     {result.wall_time_s:.3f}s")


def run_ask(
    question: str,
    *,
    project_ref: str,
    dsn: str | None,
    costs_path: Path | None,
    ann_k: int,
    hops: int,
    claim_cap: int,
    max_retries: int,
) -> int:
    """Shared implementation; returns the process exit code."""

    try:
        conn = _connect(dsn)
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(exc, 2)
        return 2
    try:
        project = _resolve_project_ref(conn, project_ref)
        meter = CostMeter(JsonlCostSink(costs_path))
        embedder = SentenceTransformerEmbedder(meter=meter, call_site=CALL_SITE_EMBED)
        drafter = GeminiClient(
            meter=meter,
            temperature=0.0,
            base_delay=QA_BASE_DELAY,
            timeout=QA_TIMEOUT_S,
        )
        result = answer_question(
            conn,
            project.id,
            question,
            embedder=embedder,
            drafter=drafter,
            meter=meter,
            ann_k=ann_k,
            hops=hops,
            claim_cap=claim_cap,
            max_retries=max_retries,
        )
    except typer.Exit:
        raise
    except (QAError, WikiMindLLMError, PsycopgError) as exc:
        _fail(exc, 1)
        return 1
    except Exception as exc:  # noqa: BLE001
        _fail(exc, 1)
        return 1
    finally:
        conn.close()

    _print_result(result, question)
    return 0


def ask(
    question: Annotated[str, typer.Argument(help="Question to answer from the compiled wiki.")],
    project: ProjectOption = None,
    dsn: DsnOption = None,
    costs_path: CostsPathOption = None,
    ann_k: Annotated[
        int,
        typer.Option(
            "--top-k",
            help=f"ANN/FTS seeds before citation filtering (default {DEFAULT_SEED_K}).",
            min=1,
        ),
    ] = DEFAULT_SEED_K,
    hops: Annotated[
        int,
        typer.Option(
            "--hops", help=f"Graph expansion depth (default {DEFAULT_HOPS}).", min=0, max=3
        ),
    ] = DEFAULT_HOPS,
    max_claims: Annotated[
        int,
        typer.Option("--max-claims", help=f"Claim cap (default {DEFAULT_CLAIM_CAP}).", min=1),
    ] = DEFAULT_CLAIM_CAP,
    max_attempts: Annotated[
        int,
        typer.Option(
            "--max-attempts",
            help=f"Drafter transport attempts on 503/timeout (default {QA_MAX_ATTEMPTS}).",
            min=1,
            max=QA_MAX_ATTEMPTS,
        ),
    ] = QA_MAX_ATTEMPTS,
) -> None:
    """Answer a question by traversing the knowledge graph and reading its claims."""

    load_dotenv()
    resolved_project = project or os.environ.get("WIKIMIND_QA_PROJECT") or DEFAULT_PROJECT
    raise typer.Exit(
        run_ask(
            question,
            project_ref=resolved_project,
            dsn=dsn,
            costs_path=costs_path,
            ann_k=ann_k,
            hops=hops,
            claim_cap=max_claims,
            max_retries=max_attempts,
        )
    )


def main() -> None:
    """Run the QA command standalone."""

    typer.run(ask)


if __name__ == "__main__":
    main()
