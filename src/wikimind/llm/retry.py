"""Exponential-backoff helper shared by the provider clients."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def call_with_retries(
    operation: Callable[[], T],
    *,
    max_attempts: int,
    is_retryable: Callable[[Exception], bool],
    sleep: Callable[[float], None] = time.sleep,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
) -> T:
    """Run ``operation`` with deterministic exponential backoff.

    ``max_attempts`` counts the initial call plus retries (the card's
    ``max_retries=3`` means at most three total attempts).  Only exceptions
    for which ``is_retryable`` returns True are retried.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            sleep(delay)

    raise AssertionError("retry loop exited unexpectedly")
