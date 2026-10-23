"""Parse layer: source files to typed, content-addressed channels."""

from __future__ import annotations

from pathlib import Path

from . import grobid as _grobid  # noqa: F401
from . import pdf_fallback as _pdf_fallback  # noqa: F401
from . import text_parser as _text_parser  # noqa: F401
from .errors import (
    GrobidError,
    GrobidUnavailableError,
    ParseError,
    UnknownParserError,
)
from .models import Channel, ChannelBuilder, ChannelKind, ParsedDocument
from .prompt import channels_for_prompt
from .registry import (
    get_parser,
    parser_name_for_path,
    parser_names,
    register,
)
from .skip import filter_known


def parse_bytes(
    data: bytes, source_filename: str, parser: str | None = None
) -> ParsedDocument:
    """Parse raw bytes with an explicit parser or the filename default."""

    name = parser or parser_name_for_path(source_filename)
    return get_parser(name)(data, source_filename)


def parse_file(
    path: str | Path, parser: str | None = None
) -> ParsedDocument:
    """Read and parse a file from disk."""

    file_path = Path(path)
    data = file_path.read_bytes()
    name = parser or parser_name_for_path(file_path)
    return get_parser(name)(data, file_path.name)


__all__ = [
    "Channel",
    "ChannelBuilder",
    "ChannelKind",
    "GrobidError",
    "GrobidUnavailableError",
    "ParseError",
    "ParsedDocument",
    "UnknownParserError",
    "channels_for_prompt",
    "filter_known",
    "get_parser",
    "parse_bytes",
    "parse_file",
    "parser_name_for_path",
    "parser_names",
    "register",
]
