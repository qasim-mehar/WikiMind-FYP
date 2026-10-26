"""Defensive token-count coercion for provider usage payloads."""

from __future__ import annotations

from typing import Any


def token_count(value: Any) -> tuple[int, bool]:
    """Return a non-negative token count and whether the value was valid."""

    if value is None or value == "":
        return 0, False
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0, False
    if count < 0:
        return 0, False
    return count, True
