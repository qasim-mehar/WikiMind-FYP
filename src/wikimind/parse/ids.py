"""Identifier and normalization primitives.

These functions are the ID contract.  They must not change once channels have
been persisted: a channel ID is content-addressed from ``kind`` and normalized
text, and a document ID is the SHA-256 of its raw bytes.
"""

from __future__ import annotations

import hashlib
import unicodedata

KIND_SEPARATOR = "\x1f"


def normalize_text(text: str) -> str:
    """Return the canonical form used to derive channel IDs.

    Exactly: NFC-normalize, then collapse every run of Unicode whitespace to a
    single ASCII space and strip leading/trailing whitespace.
    """

    return " ".join(unicodedata.normalize("NFC", text).split())


def channel_id(kind: str, normalized: str) -> str:
    """Return the SHA-256 hex digest for a channel kind and normalized text."""

    payload = kind + KIND_SEPARATOR + normalized
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def document_sha256(data: bytes) -> str:
    """Return the SHA-256 hex digest of the raw source file bytes."""

    return hashlib.sha256(data).hexdigest()
