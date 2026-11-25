"""Visible compile-stage progress, collected without changing the compile package.

The compile pipeline already tags each LLM call with ``meta["stage"]``
(analyze / write / judge). Wrapping the two model clients lets the interface
report those stages while leaving the frozen pipeline untouched.
"""

from __future__ import annotations

import threading
from typing import Any

from ..llm.protocols import LLMClient, LLMResult, ResponseSchema
from .models import JobProgress


class StageReporter:
    """Thread-safe, append-only stage log for one ingest job."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._lock = threading.Lock()
        self._state = "running"
        self._counts: dict[str, int] = {}
        self._message = ""
        self._shadow_cost_usd = 0.0
        self._claims_committed = 0

    def note(self, stage: str) -> None:
        label = " ".join(stage.split())
        if not label:
            return
        with self._lock:
            self._counts[label] = self._counts.get(label, 0) + 1

    def finish(
        self,
        *,
        shadow_cost_usd: float,
        claims_committed: int,
        message: str,
    ) -> None:
        with self._lock:
            self._state = "done"
            self._shadow_cost_usd = shadow_cost_usd
            self._claims_committed = claims_committed
            self._message = message

    def fail(self, message: str) -> None:
        with self._lock:
            self._state = "failed"
            self._message = " ".join(message.split())

    def snapshot(self) -> JobProgress:
        with self._lock:
            stages = tuple(
                label if count == 1 else f"{label} (x{count})"
                for label, count in self._counts.items()
            )
            return JobProgress(
                id=self.job_id,
                state=self._state,
                stages=stages,
                shadow_cost_usd=self._shadow_cost_usd,
                claims_committed=self._claims_committed,
                message=self._message,
            )


class ReportingClient:
    """Report the pipeline stage carried by an LLM call, then delegate unchanged."""

    def __init__(self, inner: LLMClient, reporter: StageReporter, label: str) -> None:
        self.inner = inner
        self.reporter = reporter
        self.label = label

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: ResponseSchema,
        call_site: str,
        max_retries: int = 3,
        meta: dict[str, Any] | None = None,
    ) -> LLMResult:
        stage = str((meta or {}).get("stage") or call_site)
        self.reporter.note(f"{self.label}: {stage}")
        return self.inner.complete(
            system=system,
            user=user,
            response_schema=response_schema,
            call_site=call_site,
            max_retries=max_retries,
            meta=meta,
        )
