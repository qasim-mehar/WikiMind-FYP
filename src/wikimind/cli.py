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


def main() -> None:
    """Run the WikiMind command line application."""
    app()


if __name__ == "__main__":
    main()
