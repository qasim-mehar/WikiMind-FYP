"""Settings for the local interface prototype.

The interface is deliberately pointed at a sandbox database. A hard guard
refuses the protected live corpus even if somebody exports a stray DSN, and
the default host is loopback only. Diagnostics never print the DSN.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

# Never query this database from the interface, in any mode (work card F-safety).
FORBIDDEN_DB_NAMES = frozenset({"wikimind_live_l3"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_PROJECT = "webapp-sandbox"
DEFAULT_COSTS_PATH = Path("data/webapp/costs.jsonl")
DEFAULT_UPLOAD_DIR = Path("data/webapp/uploads")


class WebConfigError(ValueError):
    """Invalid or unsafe interface configuration."""


def _assert_sandbox_dsn(dsn: str) -> None:
    """Reject the live corpus by database name, before any connection is made."""

    lowered = dsn.lower()
    for name in FORBIDDEN_DB_NAMES:
        if name in lowered:
            raise WebConfigError(
                f"refusing to start: the interface must not query the protected corpus {name}"
            )
    try:
        params = conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001 - any malformed DSN is surfaced by connect() later
        return
    if params.get("dbname") in FORBIDDEN_DB_NAMES:
        raise WebConfigError(
            "refusing to start: the interface must not query the protected live corpus"
        )


@dataclass(frozen=True, slots=True)
class WebConfig:
    """Runtime settings. ``repr`` is safe: no DSN or key is stored as a secret here."""

    db_dsn: str
    project_ref: str = DEFAULT_PROJECT
    costs_path: Path = DEFAULT_COSTS_PATH
    upload_dir: Path = DEFAULT_UPLOAD_DIR
    host: str = "127.0.0.1"
    port: int = 8000
    live: bool = False
    embedder: str = "local"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> WebConfig:
        env = dict(os.environ if environ is None else environ)
        dsn = env.get("WIKIMIND_WEB_DB") or env.get("WIKIMIND_DB") or ""
        if not dsn:
            raise WebConfigError(
                "set WIKIMIND_WEB_DB to a sandbox database (WIKIMIND_DB is used only as a fallback)"
            )
        _assert_sandbox_dsn(dsn)

        host = env.get("WIKIMIND_WEB_HOST") or "127.0.0.1"
        if host not in LOOPBACK_HOSTS and env.get("WIKIMIND_WEB_PUBLIC") != "1":
            raise WebConfigError(
                "a non-loopback bind requires WIKIMIND_WEB_PUBLIC=1; this tool has no auth"
            )

        raw_port = env.get("WIKIMIND_WEB_PORT") or "8000"
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise WebConfigError("WIKIMIND_WEB_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise WebConfigError("WIKIMIND_WEB_PORT must be between 1 and 65535")

        embedder = env.get("WIKIMIND_WEB_EMBEDDER") or "local"
        if embedder not in {"local", "fake"}:
            raise WebConfigError("WIKIMIND_WEB_EMBEDDER must be 'local' or 'fake'")

        return cls(
            db_dsn=dsn,
            project_ref=env.get("WIKIMIND_WEB_PROJECT") or DEFAULT_PROJECT,
            costs_path=Path(env.get("WIKIMIND_WEB_COSTS_PATH") or DEFAULT_COSTS_PATH),
            upload_dir=Path(env.get("WIKIMIND_WEB_UPLOAD_DIR") or DEFAULT_UPLOAD_DIR),
            host=host,
            port=port,
            live=env.get("WIKIMIND_LIVE") == "1",
            embedder=embedder,
        )

    def require_live(self) -> None:
        """Guard real provider calls; fake-injected tests never reach this."""

        if not self.live:
            raise WebConfigError(
                "real model calls are disabled; set WIKIMIND_LIVE=1 to use Add and Ask"
            )
