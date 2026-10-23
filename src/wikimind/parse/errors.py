"""Exceptions raised by the parse layer."""

from __future__ import annotations


class ParseError(Exception):
    """Base class for parse failures."""


class UnknownParserError(ParseError):
    """Raised when a parser name or file type has no registered parser."""


class GrobidError(ParseError):
    """Raised when GROBID is unavailable or returns an invalid response."""


class GrobidUnavailableError(GrobidError):
    """Raised when WIKIMIND_GROBID_URL is not configured."""
