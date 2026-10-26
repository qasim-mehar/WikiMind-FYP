"""Stable parser registry.

A parser name is part of the memoization key used by later lanes, so names are
frozen once registered: ``text``, ``pdf-fallback``, ``grobid``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from .errors import UnknownParserError
from .models import ParsedDocument

Parser = Callable[[bytes, str], ParsedDocument]

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_PARSERS: dict[str, Parser] = {}


def register(name: str) -> Callable[[Parser], Parser]:
    """Register a parser under a stable, lowercase name."""

    if not _NAME_RE.match(name):
        raise ValueError(f"invalid parser name: {name!r}")
    if name in _PARSERS:
        raise ValueError(f"parser already registered: {name!r}")

    def decorator(func: Parser) -> Parser:
        _PARSERS[name] = func
        return func

    return decorator


def get_parser(name: str) -> Parser:
    """Return a registered parser or raise UnknownParserError."""

    try:
        return _PARSERS[name]
    except KeyError as exc:
        known = ", ".join(sorted(_PARSERS)) or "(none)"
        raise UnknownParserError(
            f"unknown parser {name!r}; registered parsers: {known}"
        ) from exc


def parser_names() -> tuple[str, ...]:
    """Return registered parser names in stable sorted order."""

    return tuple(sorted(_PARSERS))


def parser_name_for_path(path: str | Path) -> str:
    """Pick the default parser for a file path.

    ``.pdf`` -> ``pdf-fallback``; ``.txt``/``.md`` -> ``text``.  GROBID is
    never selected automatically because it requires a running service.
    """

    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return "pdf-fallback"
    if suffix in {".txt", ".md"}:
        return "text"
    raise UnknownParserError(
        f"no default parser for {suffix or 'extensionless'} file; "
        "pass an explicit parser name"
    )
