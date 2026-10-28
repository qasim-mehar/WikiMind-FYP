"""The ``wikimind index`` command: incremental embedding of node versions.

Standalone::

    python -m wikimind.index.cli <project-name-or-uuid>

After the trunk CLI lands it mounts as ``wikimind index <project>``.
"""

from __future__ import annotations

from typing import Annotated

import psycopg
import typer

from wikimind.config import get_settings, redact
from wikimind.db.connection import connect
from wikimind.llm.cost import CostMeter, JsonlCostSink, format_usd
from wikimind.llm.embedder import SentenceTransformerEmbedder
from wikimind.llm.errors import CostSinkError, EmbeddingError
from wikimind.llm.protocols import Embedder

from .indexer import DEFAULT_BATCH_SIZE, INDEX_CALL_SITE, index_project, resolve_project


def _dsn_or_exit(dsn: str | None) -> str:
    resolved = dsn or get_settings().db_dsn
    if not resolved:
        typer.echo(
            "error: no database DSN configured; set WIKIMIND_DB or pass --dsn",
            err=True,
        )
        raise typer.Exit(2)
    return resolved


def _build_embedder(meter: CostMeter) -> Embedder:
    """The local bge-small embedder; the model loads lazily on first use."""

    return SentenceTransformerEmbedder(meter=meter, call_site=INDEX_CALL_SITE)


def index(
    project: Annotated[
        str,
        typer.Argument(help="Project name or UUID."),
    ],
    dsn: Annotated[
        str | None,
        typer.Option("--dsn", help="Postgres DSN; defaults to WIKIMIND_DB."),
    ] = None,
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", help="Canonical texts per embedding call."),
    ] = DEFAULT_BATCH_SIZE,
    costs_path: Annotated[
        str | None,
        typer.Option(
            "--costs-path",
            help="Cost ledger path; defaults to WIKIMIND_COSTS_PATH or costs.jsonl.",
        ),
    ] = None,
) -> None:
    """Embed every not-yet-indexed node version of one project.

    Content decides what is embedded: a node version is embedded exactly when
    no embeddings row exists for its id and the embedder's model, so re-runs
    do nothing and only new versions are embedded.
    """

    if batch_size < 1:
        typer.echo("error: --batch-size must be positive", err=True)
        raise typer.Exit(2)

    resolved = _dsn_or_exit(dsn)
    meter = CostMeter(JsonlCostSink(costs_path)) if costs_path else CostMeter()

    conn = connect(resolved, autocommit=True)
    try:
        try:
            ref = resolve_project(conn, project)
        except (KeyError, ValueError) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(2) from exc

        try:
            result = index_project(conn, ref.id, _build_embedder(meter), batch_size=batch_size)
        except (EmbeddingError, CostSinkError, RuntimeError, ValueError, psycopg.Error) as exc:
            typer.echo(f"error: {redact(exc)}", err=True)
            raise typer.Exit(2) from exc
    except psycopg.Error as exc:
        typer.echo(f"error: {redact(exc)}", err=True)
        raise typer.Exit(2) from exc
    finally:
        conn.close()

    typer.echo(f"project: {ref.name} ({ref.id})")
    typer.echo(f"model: {result.model}")
    typer.echo(
        f"node versions: {result.total} total, {result.embedded} embedded, {result.skipped} skipped"
    )
    typer.echo(f"shadow cost: {format_usd(meter.total_shadow_cost_usd)}")


def main() -> None:
    """Run the index command standalone."""
    typer.run(index)


if __name__ == "__main__":
    main()
