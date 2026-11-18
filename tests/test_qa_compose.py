"""Answer-composition tests with scripted fakes — no keys, no network, no DB."""

from __future__ import annotations

import pytest

from wikimind.llm import CostMeter, FakeLLM, InMemorySink, RetryExhaustedError, Usage
from wikimind.qa.errors import AnswerFormatError, AnswerGenerationError
from wikimind.qa.pipeline import (
    DEFAULT_CLAIM_CAP,
    QA_MAX_ATTEMPTS,
    VALIDATION_RETRIES,
    _compose_answer,
)
from wikimind.qa.prompts import (
    CALL_SITE_ANSWER,
    QA_ANSWER_PROMPT,
    AnswerPayload,
)

GOOD = {
    "answer": "The Transformer relies on attention [c-abc].",
    "sufficient": True,
    "citations": [{"claim_id": "c-abc", "document_slug": "source:deadbeef"}],
}
BAD = {"answer": "no schema here"}


def test_compose_uses_prompt_call_site_schema_and_retry_budget() -> None:
    meter = CostMeter(InMemorySink())
    fake = FakeLLM([GOOD], meter=meter, usage=Usage(10, 20))

    payload = _compose_answer(fake, "user message")

    assert payload == AnswerPayload.model_validate(GOOD)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.system == QA_ANSWER_PROMPT
    assert call.call_site == CALL_SITE_ANSWER
    assert call.response_schema is AnswerPayload
    assert call.max_retries == QA_MAX_ATTEMPTS
    assert call.meta["arm"] == "qa"
    assert len(meter.events) == 1
    assert meter.events[0].call_site == CALL_SITE_ANSWER


def test_compose_retries_once_on_schema_validation_failure() -> None:
    fake = FakeLLM([BAD, GOOD])

    payload = _compose_answer(fake, "user message")

    assert payload.sufficient is True
    assert len(fake.calls) == VALIDATION_RETRIES + 1
    assert "did not match the required JSON shape" not in fake.calls[0].user
    assert "did not match the required JSON shape" in fake.calls[1].user


def test_compose_fails_cleanly_after_one_validation_retry() -> None:
    fake = FakeLLM([BAD, BAD])

    with pytest.raises(AnswerFormatError, match="valid answer after one retry"):
        _compose_answer(fake, "user message")
    assert len(fake.calls) == 2


def test_compose_wraps_provider_failure_in_clean_qa_error() -> None:
    fake = FakeLLM([RetryExhaustedError("Gemini call failed after 3 attempts")])

    with pytest.raises(AnswerGenerationError) as excinfo:
        _compose_answer(fake, "user message")
    assert "answer generation failed" in str(excinfo.value)
    assert len(fake.calls) == 1


def test_pinned_defaults_are_stable() -> None:
    assert QA_MAX_ATTEMPTS == 3
    assert VALIDATION_RETRIES == 1
    assert DEFAULT_CLAIM_CAP == 40
