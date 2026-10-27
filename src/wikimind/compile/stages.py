"""The three distillation stages: ANALYZE, WRITE, JUDGE.

Each stage wraps one metered client call.  Validation failures (bad JSON or
a response that fails the pydantic output model) are retried exactly once
with the validation error appended to the user message; a second failure is
returned as a schema reject and counted by the pipeline.  Provider
failures (429/5xx exhaustion, transport) propagate — they fail the run and
are never silently converted into rejects.

All calls go through the Lane 2 client contract: JSON mode, pinned models,
temperature 0, canonical call sites, cost ledger via the meter.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from ..llm.errors import ResponseFormatError
from ..llm.protocols import LLMClient, LLMResult
from .models import AnalyzeOutput, JudgeOutput, WriteOutput
from .prompts import (
    ANALYZE_PROMPT,
    CALL_SITE_ANALYZE,
    CALL_SITE_JUDGE,
    CALL_SITE_WRITE,
    JUDGE_PROMPT,
    WRITE_PROMPT,
)

OutputT = TypeVar("OutputT", bound=BaseModel)

STAGE_SYSTEM_PROMPT = (
    "You are a stage of the WikiMind knowledge compiler. The pinned stage "
    "instructions and their inputs follow in the user message; return only "
    "the requested JSON object."
)

DEFAULT_INTER_CALL_DELAY = 0.0
DELAY_ENV_VAR = "WIKIMIND_COMPILE_DELAY"

_MAX_RETRIES = 3


def inter_call_delay() -> float:
    """Configurable pacing between LLM calls (operational, not a pin)."""

    raw = os.environ.get(DELAY_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_INTER_CALL_DELAY
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_INTER_CALL_DELAY
    return max(value, 0.0)


@dataclass(frozen=True, slots=True)
class StageResult(Generic[OutputT]):
    """Outcome of one stage call.

    ``schema_rejected`` marks the retry-once-then-reject path; ``error``
    carries the redacted reason for the reject.  Provider errors never
    produce a StageResult — they raise.
    """

    value: OutputT | None
    schema_rejected: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


def _complete_once(
    client: LLMClient,
    *,
    schema: type[OutputT],
    user: str,
    call_site: str,
    meta: dict[str, Any] | None,
    delay: float,
    sleep: Any = time.sleep,
) -> LLMResult:
    try:
        return client.complete(
            system=STAGE_SYSTEM_PROMPT,
            user=user,
            response_schema=schema,
            call_site=call_site,
            max_retries=_MAX_RETRIES,
            meta=meta,
        )
    finally:
        if delay > 0:
            sleep(delay)


def _validate_payload(schema: type[OutputT], payload: dict[str, Any]) -> OutputT:
    """Validate a parsed payload, normalizing failures to ResponseFormatError.

    Real clients already validate against ``response_schema`` and raise
    ``ResponseFormatError``; validating here too means fakes (which return
    scripted dicts verbatim) exercise the same retry-once-then-reject path.
    """

    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise ResponseFormatError(f"response did not match {schema.__name__}: {exc}") from exc


def _complete_with_schema_retry(
    client: LLMClient,
    *,
    schema: type[OutputT],
    user: str,
    call_site: str,
    meta: dict[str, Any] | None,
    delay: float,
    sleep: Any = time.sleep,
) -> StageResult[OutputT]:
    """Call once; on a schema/JSON failure retry once with the error appended."""

    try:
        first = _complete_once(
            client,
            schema=schema,
            user=user,
            call_site=call_site,
            meta=meta,
            delay=delay,
            sleep=sleep,
        )
        return StageResult(value=_validate_payload(schema, first.json))
    except ResponseFormatError as first_error:
        retry_user = (
            f"{user}\n\nYour previous response was not valid: {first_error}. "
            "Return only the JSON object matching the requested shape."
        )
    try:
        second = _complete_once(
            client,
            schema=schema,
            user=retry_user,
            call_site=call_site,
            meta=meta,
            delay=delay,
            sleep=sleep,
        )
        return StageResult(value=_validate_payload(schema, second.json))
    except ResponseFormatError as second_error:
        return StageResult(value=None, schema_rejected=True, error=str(second_error))


def run_analyze(
    client: LLMClient,
    *,
    passage_block: str,
    call_site: str = CALL_SITE_ANALYZE,
    meta: dict[str, Any] | None = None,
    delay: float = 0.0,
    sleep: Any = time.sleep,
) -> StageResult[AnalyzeOutput]:
    """ANALYZE: passage -> candidate atomic claims."""

    user = f"{ANALYZE_PROMPT}\n\n{passage_block}"
    return _complete_with_schema_retry(
        client,
        schema=AnalyzeOutput,
        user=user,
        call_site=call_site,
        meta=meta,
        delay=delay,
        sleep=sleep,
    )


def run_write(
    client: LLMClient,
    *,
    candidate_block: str,
    neighborhood_block: str,
    call_site: str = CALL_SITE_WRITE,
    meta: dict[str, Any] | None = None,
    delay: float = 0.0,
    sleep: Any = time.sleep,
) -> StageResult[WriteOutput]:
    """WRITE: candidate + existing graph neighborhood -> final claim text."""

    user = f"{WRITE_PROMPT}\n\n{candidate_block}\n\n{neighborhood_block}"
    return _complete_with_schema_retry(
        client,
        schema=WriteOutput,
        user=user,
        call_site=call_site,
        meta=meta,
        delay=delay,
        sleep=sleep,
    )


def run_judge(
    client: LLMClient,
    *,
    passage_block: str,
    claim_block: str,
    call_site: str = CALL_SITE_JUDGE,
    meta: dict[str, Any] | None = None,
    delay: float = 0.0,
    sleep: Any = time.sleep,
) -> StageResult[JudgeOutput]:
    """JUDGE: passage + FINAL claim text -> entailment verdict."""

    user = f"{JUDGE_PROMPT}\n\n{passage_block}\n\n{claim_block}"
    return _complete_with_schema_retry(
        client,
        schema=JudgeOutput,
        user=user,
        call_site=call_site,
        meta=meta,
        delay=delay,
        sleep=sleep,
    )


__all__ = [
    "StageResult",
    "inter_call_delay",
    "run_analyze",
    "run_judge",
    "run_write",
]
