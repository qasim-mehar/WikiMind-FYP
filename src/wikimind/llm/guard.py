"""Hard max-output-token guard for generation calls (Gate C / Ruling 30).

A generation attempt whose reported output length exceeds the configured cap is
a FAILED attempt: its text is discarded, its full token usage is metered with an
``output_cap_exceeded`` marker (nothing hidden), and the existing retry policy
re-attempts.  This layer never truncates output silently.  Embedding calls are
out of scope and never consult this guard.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..config import load_dotenv
from .errors import OutputCapExceededError

if TYPE_CHECKING:
    from .cost import CostMeter

DEFAULT_MAX_OUTPUT_TOKENS = 4096
MAX_OUTPUT_TOKENS_ENV = "WIKIMIND_MAX_OUTPUT_TOKENS"


def max_output_tokens_from_env(environ: Mapping[str, str] | None = None) -> int:
    """Resolve the hard cap through the existing env / ``.env`` mechanism."""

    if environ is None:
        load_dotenv()
        environ = os.environ
    raw = environ.get(MAX_OUTPUT_TOKENS_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_MAX_OUTPUT_TOKENS
    try:
        cap = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{MAX_OUTPUT_TOKENS_ENV} must be a positive integer") from None
    if cap < 1:
        raise ValueError(f"{MAX_OUTPUT_TOKENS_ENV} must be a positive integer")
    return cap


def normalize_output_cap(value: int | None) -> int:
    """Validate an explicit cap, falling back to the env-configured default."""

    if value is None:
        return max_output_tokens_from_env()
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("output_token_cap must be a positive integer")
    return value


def enforce_output_cap(*, out_tokens: int, cap: int, call_site: str, model: str) -> None:
    """Raise a retryable error when an attempt exceeds the hard cap."""

    if out_tokens > cap:
        raise OutputCapExceededError(
            f"generation exceeded hard output-token cap: {out_tokens} > {cap} "
            f"(call_site={call_site}, model={model})"
        )


def meter_cap_hit(
    meter: CostMeter,
    *,
    call_site: str,
    model: str,
    in_tokens: int,
    out_tokens: int,
    cap: int,
    meta: dict[str, Any] | None = None,
) -> None:
    """Log a failed over-cap attempt with its full usage (nothing hidden)."""

    event_meta: dict[str, Any] = dict(meta or {})
    event_meta["output_cap_exceeded"] = True
    event_meta["output_cap"] = cap
    meter.validate_meta(event_meta)
    meter.record(
        call_site=call_site,
        model=model,
        in_tokens=in_tokens,
        out_tokens=out_tokens,
        meta=event_meta,
    )
