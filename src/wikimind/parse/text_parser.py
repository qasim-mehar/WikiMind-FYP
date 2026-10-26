"""Plain-text and Markdown parser.

The entire file becomes one ``section`` channel.  Files must be UTF-8 (a UTF-8
BOM is stripped).  A whitespace-only file produces a document with no channels.
"""

from __future__ import annotations

from .errors import ParseError
from .models import ChannelBuilder, ParsedDocument, make_document
from .registry import register


@register("text")
def parse_text(data: bytes, source_filename: str) -> ParsedDocument:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(
            f"{source_filename}: text parser requires UTF-8 input (byte {exc.start} is invalid)"
        ) from exc

    builder = ChannelBuilder()
    if text.strip():
        builder.add("section", text)
    return make_document(
        "text",
        source_filename,
        data,
        builder.finish(),
        page_count=None,
    )
