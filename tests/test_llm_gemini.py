from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from google.genai.errors import ClientError, ServerError
from pydantic import BaseModel

from wikimind.llm import (
    CostMeter,
    CostSinkError,
    GeminiClient,
    GeminiError,
    InMemorySink,
    MissingAPIKeyError,
    PricingError,
    ResponseFormatError,
    RetryExhaustedError,
    Usage,
)
from wikimind.prices import GEMINI_MODEL


@dataclass
class FakeUsage:
    prompt_token_count: int = 10
    candidates_token_count: int = 20
    thoughts_token_count: int = 5


class FakeResponse:
    def __init__(
        self,
        text: str = '{"ok": true}',
        parsed: Any = None,
        usage: FakeUsage | None = None,
    ) -> None:
        self.text = text
        self.parsed = parsed
        self.usage_metadata = usage or FakeUsage()
        self.model_version = "gemini-2.5-flash-001"
        self.response_id = "resp-1"


class FakeModels:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
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


class Output(BaseModel):
    ok: bool


class DatedOutput(BaseModel):
    when: datetime


def make_client(outcomes: list[Any], meter: CostMeter, **kwargs: Any):
    fake = FakeGenaiClient(outcomes)
    client = GeminiClient(meter=meter, client=fake, **kwargs)
    return client, fake


def test_success_parses_json_usage_and_meter_event() -> None:
    meter = CostMeter(InMemorySink())
    client, fake = make_client([FakeResponse()], meter)

    result = client.complete(
        system="system prompt",
        user="user prompt",
        response_schema={"type": "object"},
        call_site="l1.test",
        meta={"arm": "B"},
    )

    assert result.json == {"ok": True}
    assert result.usage == Usage(in_tokens=10, out_tokens=25)
    assert len(fake.models.calls) == 1
    config = fake.models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == {"type": "object"}
    assert config.system_instruction == "system prompt"

    event = meter.events[0]
    assert event.call_site == "l1.test"
    assert event.model == GEMINI_MODEL
    assert event.in_tokens == 10
    assert event.out_tokens == 25
    assert event.meta["arm"] == "B"
    assert event.meta["provider"] == "google-genai"
    assert event.meta["thinking_tokens"] == 5
    assert event.meta["model_version"] == "gemini-2.5-flash-001"


def test_pydantic_schema_uses_response_schema() -> None:
    meter = CostMeter(InMemorySink())
    client, fake = make_client([FakeResponse()], meter)
    client.complete(
        system="s",
        user="u",
        response_schema=Output,
        call_site="qa.test",
    )
    config = fake.models.calls[0]["config"]
    assert config.response_schema is Output


def test_retries_429_and_5xx_with_exponential_backoff() -> None:
    meter = CostMeter(InMemorySink())
    delays: list[float] = []
    client, fake = make_client(
        [
            ClientError(429, {"message": "rate limited"}),
            ServerError(503, {"message": "unavailable"}),
            FakeResponse(),
        ],
        meter,
        sleep=delays.append,
    )

    result = client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="qa.test",
    )

    assert result.json == {"ok": True}
    assert len(fake.models.calls) == 3
    assert delays == [0.5, 1.0]
    assert len(meter.events) == 1
    assert meter.events[0].meta["parse_ok"] is True


def test_does_not_retry_other_4xx() -> None:
    meter = CostMeter(InMemorySink())
    delays: list[float] = []
    client, fake = make_client(
        [ClientError(400, {"message": "bad request"})],
        meter,
        sleep=delays.append,
    )

    with pytest.raises(GeminiError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
        )

    assert len(fake.models.calls) == 1
    assert delays == []


def test_exhausts_three_attempts() -> None:
    meter = CostMeter(InMemorySink())
    delays: list[float] = []
    client, fake = make_client(
        [ServerError(500, {"message": "boom"})] * 3,
        meter,
        sleep=delays.append,
    )

    with pytest.raises(RetryExhaustedError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
        )

    assert len(fake.models.calls) == 3
    assert delays == [0.5, 1.0]
    assert meter.events == []


def test_missing_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    meter = CostMeter(InMemorySink())
    client = GeminiClient(meter=meter)
    with pytest.raises(MissingAPIKeyError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
        )


def test_parses_fenced_json() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        [FakeResponse(text='```json\n{"ok": true}\n```')],
        meter,
    )
    result = client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="qa.test",
    )
    assert result.json == {"ok": True}


def test_rejects_non_json_response() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client([FakeResponse(text="not json")], meter)
    with pytest.raises(ResponseFormatError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
        )
    assert len(meter.events) == 1
    assert meter.events[0].meta["parse_ok"] is False
    assert meter.events[0].in_tokens == 10
    assert meter.events[0].out_tokens == 25


def test_pydantic_validation_failure_still_meters() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client([FakeResponse(text='{"wrong": true}')], meter)
    with pytest.raises(ResponseFormatError):
        client.complete(
            system="s",
            user="u",
            response_schema=Output,
            call_site="qa.test",
        )
    assert len(meter.events) == 1
    assert meter.events[0].meta["parse_ok"] is True
    assert meter.events[0].meta["schema_checked"] is True
    assert meter.events[0].meta["schema_ok"] is False


def test_unpriced_model_fails_before_request() -> None:
    meter = CostMeter(InMemorySink())
    fake = FakeGenaiClient([FakeResponse()])
    with pytest.raises(PricingError):
        GeminiClient(meter=meter, client=fake, model="unknown/model")
    assert fake.models.calls == []


def test_max_retries_is_capped_at_three() -> None:
    meter = CostMeter(InMemorySink())
    client, fake = make_client([FakeResponse()], meter)
    with pytest.raises(ValueError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
            max_retries=4,
        )
    assert fake.models.calls == []


def test_non_json_constructor_meta_fails_before_request() -> None:
    meter = CostMeter(InMemorySink())
    fake = FakeGenaiClient([FakeResponse()])
    client = GeminiClient(meter=meter, client=fake, meta={"bad": object()})
    with pytest.raises(CostSinkError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
        )
    assert fake.models.calls == []


def test_sink_preflight_failure_prevents_request() -> None:
    class FailingSink:
        def preflight(self) -> None:
            raise CostSinkError("ledger unavailable")

        def emit(self, event: Any) -> None:
            raise AssertionError("emit must not be called")

    fake = FakeGenaiClient([FakeResponse()])
    client = GeminiClient(meter=CostMeter(FailingSink()), client=fake)
    with pytest.raises(CostSinkError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
        )
    assert fake.models.calls == []


def test_missing_usage_is_flagged() -> None:
    meter = CostMeter(InMemorySink())
    response = FakeResponse()
    response.usage_metadata = None
    client, _ = make_client([response], meter)
    client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="qa.test",
    )
    assert meter.events[0].meta["usage_reported"] is False
    assert meter.events[0].meta["usage_invalid"] is True
    assert meter.events[0].in_tokens == 0
    assert meter.events[0].out_tokens == 0


def test_malformed_usage_is_zeroed_and_metered() -> None:
    meter = CostMeter(InMemorySink())
    response = FakeResponse()
    response.usage_metadata = FakeUsage(prompt_token_count="not-a-number")
    client, _ = make_client([response], meter)
    result = client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="qa.test",
    )
    assert result.json == {"ok": True}
    assert meter.events[0].in_tokens == 0
    assert meter.events[0].out_tokens == 25
    assert meter.events[0].meta["usage_invalid"] is True


def test_absent_thought_tokens_do_not_invalidate_usage() -> None:
    @dataclass
    class UsageWithoutThoughts:
        prompt_token_count: int = 10
        candidates_token_count: int = 20

    meter = CostMeter(InMemorySink())
    response = FakeResponse()
    response.usage_metadata = UsageWithoutThoughts()
    client, _ = make_client([response], meter)
    client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="qa.test",
    )
    event = meter.events[0]
    assert event.in_tokens == 10
    assert event.out_tokens == 20
    assert event.meta["thinking_tokens"] == 0
    assert event.meta["usage_reported"] is True
    assert event.meta["usage_invalid"] is False


def test_pydantic_datetime_is_json_safe() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        [FakeResponse(text='{"when": "2026-10-23T12:00:00Z"}')],
        meter,
    )
    result = client.complete(
        system="s",
        user="u",
        response_schema=DatedOutput,
        call_site="qa.test",
    )
    assert isinstance(result.json["when"], str)


def test_explicit_key_is_redacted(monkeypatch) -> None:
    key = "AIza" + "R" * 30
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        [ClientError(400, {"message": f"rejected credential {key}"})],
        meter,
        api_key=key,
    )
    with pytest.raises(GeminiError) as excinfo:
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
        )
    assert key not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)


def test_key_never_appears_in_exception(monkeypatch) -> None:
    key = "AIza" + "Q" * 30
    monkeypatch.setenv("GEMINI_API_KEY", key)
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        [ClientError(400, {"message": f"rejected credential {key}"})],
        meter,
    )

    with pytest.raises(GeminiError) as excinfo:
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="qa.test",
        )

    assert key not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)
