"""Secret loading and redaction.

Keys are read from the process environment only.  A gitignored ``.env`` file
may be loaded into the environment by the CLI, but values are never logged,
printed, or included in exception messages.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
_KNOWN_KEY_ENVS = (GEMINI_API_KEY_ENV, OPENROUTER_API_KEY_ENV)

_KEY_VALUE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-or-v1-[0-9A-Za-z_\-]{16,}"),
    re.compile(r"(?i)(bearer\s+)\S+"),
    re.compile(r"(?i)(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+"),
)


def get_key(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def gemini_api_key() -> str | None:
    return get_key(GEMINI_API_KEY_ENV)


def openrouter_api_key() -> str | None:
    return get_key(OPENROUTER_API_KEY_ENV)


def require_key(name: str) -> str:
    value = get_key(name)
    if value is None:
        from .errors import MissingAPIKeyError

        raise MissingAPIKeyError(f"{name} is not set")
    return value


def load_dotenv(path: str | Path | None = None) -> bool:
    """Load simple KEY=VALUE pairs into os.environ without overriding.

    Returns True when a file was found and parsed.  Values are never logged.
    Only well-formed environment variable names are accepted.
    """

    env_path = (
        Path(path)
        if path is not None
        else Path(os.environ.get("WIKIMIND_ENV_FILE", ".env"))
    )
    if not env_path.is_file():
        return False

    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _KEY_VALUE_RE.match(line)
        if match is None:
            continue
        name, value = match.groups()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if not os.environ.get(name):
            os.environ[name] = value
    return True


def _secret_values() -> list[str]:
    values: list[str] = []
    for name in _KNOWN_KEY_ENVS:
        value = os.environ.get(name)
        if value:
            values.append(value)
    for name, value in os.environ.items():
        if name.endswith("_API_KEY") and value and value not in values:
            values.append(value)
    return values


def redact(text: str, secrets: list[str] | None = None) -> str:
    """Replace known secret values and common key shapes in ``text``."""

    if not text:
        return text

    values = _secret_values() if secrets is None else list(secrets)
    redacted = text
    for value in sorted(
        (v for v in values if v and len(v) >= 4), key=len, reverse=True
    ):
        redacted = redacted.replace(value, "[REDACTED]")

    redacted = _SECRET_PATTERNS[0].sub("[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[1].sub("[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[2].sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_PATTERNS[3].sub(r"\1[REDACTED]", redacted)
    return redacted


def redact_secrets(text: str, extra: list[str] | None = None) -> str:
    """Redact known key values and API-key-shaped substrings."""

    return redact(text, extra)
