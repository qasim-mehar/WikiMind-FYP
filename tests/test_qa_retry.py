"""Q4: the QA drafter's transport retry curve is three attempts, 0.5s then 1.0s."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from google.genai.errors import ClientError, ServerError

from wikimind.llm import CostMeter, GeminiClient, InMemorySink, RetryExhaustedError
from wikimind.qa.pipeline import QA_BASE_DELAY, QA_MAX_ATTEMPTS, QA_TIMEOUT_S
from wikimind.qa.prompts import CALL_SITE_ANSWER, QA_ANSWER_PROMPT, AnswerPayload

VALID_JSON = '{"answer": "ok [c-1]", "sufficient": true, "citations": []}'


@dataclass
class FakeUsage:
    prompt_token_count: int = 10
    candidates_token_count: int = 20
    thoughts_token_count: int = 0


class FakeResponse:
    text = VALID_JSON
    parsed = None
    usage_metadata = FakeUsage()
    model_version = "gemini-3.1-flash-lite-001"
    response_id = "resp-qa"


class FakeModels:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeGenaiClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.models = FakeModels(outcomes)


def _client(outcomes: list[Any], meter: CostMeter, sleeps: list[float]):
    fake = FakeGenaiClient(outcomes)
    client = GeminiClient(
        meter=meter,
        client=fake,
        base_delay=QA_BASE_DELAY,
        timeout=QA_TIMEOUT_S,
        sleep=sleeps.append,
    )
    return client, fake


def _call(client: GeminiClient):
    return client.complete(
        system=QA_ANSWER_PROMPT,
        user="question with claims",
        response_schema=AnswerPayload,
        call_site=CALL_SITE_ANSWER,
        max_retries=QA_MAX_ATTEMPTS,
    )


def test_pin_values_are_stable() -> None:
    assert QA_MAX_ATTEMPTS == 3
    assert QA_BASE_DELAY == 0.5
    assert QA_TIMEOUT_S == 120.0


def test_retries_503_and_429_with_pinned_exponential_backoff() -> None:
    meter = CostMeter(InMemorySink())
    delays: list[float] = []
    client, fake = _client(
        [
            ServerError(503, {"message": "unavailable"}),
            ClientError(429, {"message": "rate limited"}),
            FakeResponse(),
        ],
        meter,
        delays,
    )

    result = _call(client)

    assert result.json["sufficient"] is True
    assert len(fake.models.calls) == 3
    assert delays == [0.5, 1.0]
    assert len(meter.events) == 1  # only the successful attempt is metered


def test_exhausts_three_attempts_then_raises_retry_exhausted() -> None:
    meter = CostMeter(InMemorySink())
    delays: list[float] = []
    client, fake = _client(
        [ServerError(503, {"message": "down"})] * QA_MAX_ATTEMPTS,
        meter,
        delays,
    )

    with pytest.raises(RetryExhaustedError):
        _call(client)

    assert len(fake.models.calls) == 3
    assert delays == [0.5, 1.0]
    assert meter.events == []
