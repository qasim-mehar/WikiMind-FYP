"""Pure skip-unchanged filter; the caller supplies the known SHA set."""

from __future__ import annotations

from collections.abc import Iterable

from .models import ParsedDocument


def filter_known(
    documents: Iterable[ParsedDocument], known_sha256s: set[str]
) -> list[ParsedDocument]:
    """Drop documents whose raw-byte SHA-256 is already known.

    The input is not mutated and document order is preserved.  No database or
    I/O is performed here; a caller wires the persisted SHA set later.
    """

    return [doc for doc in documents if doc.source_sha256 not in known_sha256s]
