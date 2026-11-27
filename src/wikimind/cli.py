"""WikiMind command line entry point.

Areas mount themselves through ``<area>/cli.py``.  A missing area is normal
while lanes are being built in parallel, so only ``ImportError`` is ignored.
"""

from __future__ import annotations

from typing import Annotated

import typer

from wikimind.config import (
    cost_mirror_requested,
    get_settings,
    protected_cost_mirror_database,
)
from wikimind.db import cli as db_cli

app = typer.Typer(
    name="wikimind",
    help="Compile source documents into a source-tracked, versioned knowledge graph.",
    no_args_is_help=True,
)


def cost_mirror_guard_message(*, force: bool = False) -> str | None:
    """Refusal text when a mirror would write to the live corpus, else None.

    The guard lives in the entry layer so the frozen cost meter stays untouched:
    it inspects the *target* DSN before any command opens a connection and
    before a MirroringCostSink can append rows to ``cost_events``.
    """

    if force or not cost_mirror_requested():
        return None
    dsn = get_settings().db_dsn
    if not dsn:
        return None
    protected = protected_cost_mirror_database(dsn)
    if protected is None:
        return None
    return (
        f"WIKIMIND_COST_DB_MIRROR=1 would mirror cost events into the protected "
        f"corpus database {protected!r}; re-run with --force if intentional"
    )


@app.callback()
def root_callback(
    force: Annotated[
        bool,
        typer.Option("--force", help="Override the live-corpus cost-mirror guard."),
    ] = False,
) -> None:
    """Guard the live corpus against accidental cost_events mirroring."""

    message = cost_mirror_guard_message(force=force)
    if message is not None:
        typer.echo(f"error: {message}", err=True)
        raise typer.Exit(2)


app.add_typer(db_cli.app, name="db")

try:
    from wikimind.parse import cli as parse_cli
except ModuleNotFoundError as exc:
    if exc.name not in {"wikimind.parse", "wikimind.parse.cli"}:
        raise
else:
    app.add_typer(parse_cli.app, name="parse")

try:
    from wikimind.llm import cli as llm_cli
except ModuleNotFoundError as exc:
    if exc.name not in {"wikimind.llm", "wikimind.llm.cli"}:
        raise
else:
    app.add_typer(llm_cli.app, name="llm")

try:
    from wikimind.index import cli as index_cli
except ModuleNotFoundError as exc:
    if exc.name not in {"wikimind.index", "wikimind.index.cli"}:
        raise
else:
    app.command(name="index")(index_cli.index)

try:
    from wikimind.compile import cli as compile_cli
except ModuleNotFoundError as exc:
    if exc.name not in {"wikimind.compile", "wikimind.compile.cli"}:
        raise
else:
    # `wikimind compile <project>` is a command, not a sub-app; the project
    # and document registration helpers mount as their own groups.
    app.command(name="compile")(compile_cli.compile_cmd)
    app.add_typer(compile_cli.project_app, name="project")
    app.add_typer(compile_cli.document_app, name="document")

try:
    from wikimind.ingest import cli as ingest_cli
except ModuleNotFoundError as exc:
    if exc.name not in {"wikimind.ingest", "wikimind.ingest.cli"}:
        raise
else:
    # ``wikimind add <project> <files...>`` is a top-level command: one visible
    # flow for extract -> register -> estimate -> confirm -> compile.
    app.command(name="add")(ingest_cli.add_cmd)

try:
    from wikimind.render import cli as render_cli
except ModuleNotFoundError as exc:
    if exc.name not in {"wikimind.render", "wikimind.render.cli"}:
        raise
else:
    app.add_typer(render_cli.app, name="render")

try:
    from wikimind.qa import cli as qa_cli
except ModuleNotFoundError as exc:
    if exc.name not in {"wikimind.qa", "wikimind.qa.cli"}:
        raise
else:
    # ``wikimind ask "<question>"`` is a top-level command, like compile/index.
    app.command(name="ask")(qa_cli.ask)

try:
    from wikimind.integrity import cli as integrity_cli
except ModuleNotFoundError as exc:
    if exc.name not in {"wikimind.integrity", "wikimind.integrity.cli"}:
        raise
else:
    app.add_typer(integrity_cli.app, name="integrity")


def main() -> None:
    """Run the WikiMind command line application."""
    app()


if __name__ == "__main__":
    main()
