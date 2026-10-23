"""Configuration loaded from the process environment and a local ``.env`` file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DSN_PASSWORD = re.compile(r"(?i)(postgres(?:ql)?://[^:/@\s]+:)([^@\s]+)(@)")
_INLINE_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|api[_-]?key|secret|token)\b(\s*[=:]\s*)([^&\s,;]+)"
)
_BEARER = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)")

DEFAULT_ENV_FILE = ".env"


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Load simple ``KEY=VALUE`` entries from ``.env`` without a dependency.

    Existing environment variables win unless ``override`` is true.  Values may
    be single- or double-quoted.  The function never prints or logs values.
    """

    env_path = Path(path) if path is not None else Path.cwd() / DEFAULT_ENV_FILE
    if not env_path.is_file():
        return None

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY.fullmatch(key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value

    return env_path


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings.  Its repr is redacted so it is safe to log."""

    db_dsn: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    grobid_url: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_dsn=os.environ.get("WIKIMIND_DB") or None,
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or None,
            grobid_url=os.environ.get("WIKIMIND_GROBID_URL") or None,
        )

    def __repr__(self) -> str:
        return (
            "Settings("
            f"db_dsn={redact(self.db_dsn)!r}, "
            f"gemini_api_key={redact_secret(self.gemini_api_key)!r}, "
            f"openrouter_api_key={redact_secret(self.openrouter_api_key)!r}, "
            f"grobid_url={redact(self.grobid_url)!r}"
            ")"
        )

    __str__ = __repr__


def get_settings(*, load_env: bool = True) -> Settings:
    """Return settings, loading ``.env`` first unless disabled."""

    if load_env:
        load_dotenv()
    return Settings.from_env()


def redact(value: object) -> str:
    """Redact credentials from a DSN or other diagnostic string."""

    if value is None:
        return ""
    text = str(value)
    text = _DSN_PASSWORD.sub(r"\1***\3", text)
    text = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    text = _BEARER.sub(r"\1***", text)
    return text


def redact_secret(value: object, *, keep: int = 4) -> str:
    """Redact an opaque secret, retaining only a short suffix for debugging."""

    if value is None:
        return ""
    text = str(value)
    if len(text) <= keep:
        return "***"
    return "***" + text[-keep:]
