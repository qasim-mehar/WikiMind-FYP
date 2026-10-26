"""Stable content IDs shared by ingestion lanes."""

from __future__ import annotations

import hashlib

CHANNEL_ID_SEPARATOR = "\x1f"


def channel_id(kind: str, normalized_text: str) -> str:
    """Return the content-hash ID for a channel.

    The separator is ASCII unit separator (``0x1f``).  The exact formula is
    part of the schema contract: ``sha256(kind || sep || normalized_text)``.
    """

    payload = f"{kind}{CHANNEL_ID_SEPARATOR}{normalized_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    """Hash UTF-8 text, used by callers for source and memoization keys."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
