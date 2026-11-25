"""WikiMind command line entry point.

Areas mount themselves through ``<area>/cli.py``.  A missing area is normal
while lanes are being built in parallel, so only ``ImportError`` is ignored.
"""

from __future__ import annotations

import typer

from wikimind.db import cli as db_cli

app = typer.Typer(
    name="wikimind",
    help="Compile source documents into a source-tracked, versioned knowledge graph.",
    no_args_is_help=True,
)
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
