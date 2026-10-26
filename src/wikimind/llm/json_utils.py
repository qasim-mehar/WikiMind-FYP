"""Strict-ish JSON object parsing for provider responses."""

from __future__ import annotations

import json
from typing import Any


def json_safe_value(value: Any) -> Any:
    """Coerce provider metadata into a JSON-safe scalar."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def parse_json_object(text: str | None) -> dict[str, Any]:
    """Parse a JSON object, tolerating markdown fences around it."""

    if text is None or not text.strip():
        raise ValueError("empty response")

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            cleaned = "\n".join(lines[1:])
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        parsed = None
        for index, character in enumerate(cleaned):
            if character != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(cleaned[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                break
        if parsed is None:
            raise ValueError("response did not contain a valid JSON object") from None

    if not isinstance(parsed, dict):
        raise ValueError("response JSON was not an object")  # noqa: TRY004
    return parsed
