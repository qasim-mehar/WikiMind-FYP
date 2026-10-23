"""Strict-ish JSON object parsing for provider responses."""

from __future__ import annotations

import json
from typing import Any


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
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("response did not contain a JSON object") from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            raise ValueError("response did not contain valid JSON") from None

    if not isinstance(parsed, dict):
        raise ValueError("response JSON was not an object")  # noqa: TRY004
    return parsed
