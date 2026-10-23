"""Typer sub-app for the parse layer.

Standalone::

    python -m wikimind.parse.cli run tests/fixtures/papers/1706.03762-attention.pdf --json
    python -m wikimind.parse.cli grobid-check

After the trunk CLI lands it mounts as ``wikimind parse ...``.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from . import parse_file
from .errors import ParseError
from .grobid import probe_grobid
from .models import ChannelKind
from .registry import parser_names

app = typer.Typer(help="Parse source files into typed, content-addressed channels.")


def _ensure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


@app.command()
def run(
    file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            readable=True,
            dir_okay=False,
            help="Source file to parse (.pdf, .txt, or .md).",
        ),
    ],
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Print the ParsedDocument as JSON.",
        ),
    ] = False,
    parser: Annotated[
        str | None,
        typer.Option(
            "--parser",
            "-p",
            help=f"Explicit parser name ({', '.join(parser_names())}).",
        ),
    ] = None,
) -> None:
    """Parse one file and print its channels."""

    try:
        document = parse_file(file, parser=parser)
    except (ParseError, OSError) as exc:
        typer.echo(f"parse error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(document.model_dump_json())
        return

    counts = Counter(channel.kind.value for channel in document.channels)
    lines = [
        f"parser: {document.parser}",
        f"source: {document.source_filename}",
        f"sha256: {document.source_sha256}",
        f"bytes: {document.source_bytes}",
        f"channels: {len(document.channels)}",
    ]
    for kind in ChannelKind:
        if counts[kind.value]:
            lines.append(f"  {kind.value}: {counts[kind.value]}")
    typer.echo("\n".join(lines))


@app.command("grobid-check")
def grobid_check() -> None:
    """Probe the GROBID service at WIKIMIND_GROBID_URL."""

    ok, detail = probe_grobid()
    typer.echo(detail)
    if not ok:
        raise typer.Exit(code=1)


def main() -> None:
    _ensure_utf8_streams()
    app()


if __name__ == "__main__":
    main()
