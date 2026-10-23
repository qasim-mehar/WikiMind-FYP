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
except ImportError:
    parse_cli = None
else:
    app.add_typer(parse_cli.app, name="parse")

try:
    from wikimind.llm import cli as llm_cli
except ImportError:
    llm_cli = None
else:
    app.add_typer(llm_cli.app, name="llm")


def main() -> None:
    """Run the WikiMind command line application."""
    app()


if __name__ == "__main__":
    main()
