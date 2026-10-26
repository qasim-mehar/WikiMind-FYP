from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from wikimind.llm import (
    CostMeter,
    CostSinkError,
    InMemorySink,
    JudgeClient,
    JudgeError,
    MissingAPIKeyError,
    PricingError,
    ResponseFormatError,
    RetryExhaustedError,
    Usage,
)
from wikimind.llm.judge import OPENROUTER_CHAT_URL
from wikimind.prices import JUDGE_MODEL, JUDGE_SHADOW_MODEL, JUDGE_SHADOW_PRICE


class Verdict(BaseModel):
    score: int
    reason: str


class DatedVerdict(BaseModel):
    when: datetime


def success_body(
    content: str = '{"score": 5, "reason": "ok"}',
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 20,
) -> dict[str, Any]:
    return {
        "id": "gen-1",
        "model": JUDGE_MODEL,
        "choices": [
            {
                "message": {"content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "completion_tokens_details": {"reasoning_tokens": 5},
        },
    }


def make_client(
    handler,
    meter: CostMeter,
    **kwargs: Any,
) -> tuple[JudgeClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    http_client = httpx.Client(transport=httpx.MockTransport(recording_handler))
    api_key = kwargs.pop("api_key", "test-openrouter-key")
    client = JudgeClient(
        meter=meter,
        api_key=api_key,
        http_client=http_client,
        **kwargs,
    )
    return client, requests


def test_success_request_shape_and_shadow_cost() -> None:
    meter = CostMeter(InMemorySink())
    client, requests = make_client(
        lambda request: httpx.Response(200, json=success_body()),
        meter,
    )

    result = client.complete(
        system="judge system",
        user="judge user",
        response_schema={"type": "object"},
        call_site="judge.test",
        meta={"arm": "A"},
    )

    assert result.json == {"score": 5, "reason": "ok"}
    assert result.usage == Usage(in_tokens=100, out_tokens=20)
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == OPENROUTER_CHAT_URL
    assert request.headers["Authorization"] == "Bearer test-openrouter-key"
    payload = json.loads(request.content)
    assert payload["model"] == JUDGE_MODEL
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][1] == {"role": "user", "content": "judge user"}
    assert "judge system" in payload["messages"][0]["content"]
    assert '"type": "object"' in payload["messages"][0]["content"]

    event = meter.events[0]
    assert event.call_site == "judge.test"
    assert event.model == JUDGE_MODEL
    assert event.unit_price == JUDGE_SHADOW_PRICE
    assert event.shadow_cost_usd == 0.000078
    assert event.meta["arm"] == "A"
    assert event.meta["provider"] == "openrouter"
    assert event.meta["pricing_basis"] == "paid_twin"
    assert event.meta["paid_twin"] == JUDGE_SHADOW_MODEL
    assert event.meta["actual_cost_usd"] == 0.0
    assert event.meta["reasoning_tokens"] == 5
    assert event.meta["usage_reported"] is True
    assert event.meta["usage_invalid"] is False
    assert event.meta["parse_ok"] is True


def test_caller_provider_tag_is_respected() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        lambda request: httpx.Response(200, json=success_body()),
        meter,
    )
    client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="judge.test",
        meta={"provider": "openrouter-eu"},
    )
    assert meter.events[0].meta["provider"] == "openrouter-eu"


def test_pydantic_schema_is_in_system_prompt() -> None:
    meter = CostMeter(InMemorySink())
    client, requests = make_client(
        lambda request: httpx.Response(200, json=success_body()),
        meter,
    )
    client.complete(
        system="system",
        user="user",
        response_schema=Verdict,
        call_site="judge.test",
    )
    payload = json.loads(requests[0].content)
    assert "score" in payload["messages"][0]["content"]


def test_retries_429_then_succeeds() -> None:
    meter = CostMeter(InMemorySink())
    outcomes = [
        httpx.Response(429, text="slow down"),
        httpx.Response(200, json=success_body()),
    ]
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return outcomes.pop(0)

    client, requests = make_client(handler, meter, sleep=delays.append)
    result = client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="judge.test",
    )

    assert result.json["score"] == 5
    assert len(requests) == 2
    assert delays == [0.5]
    assert len(meter.events) == 1


def test_retries_5xx_then_succeeds() -> None:
    meter = CostMeter(InMemorySink())
    outcomes = [
        httpx.Response(502, text="bad gateway"),
        httpx.Response(200, json=success_body()),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return outcomes.pop(0)

    client, requests = make_client(handler, meter)
    result = client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="judge.test",
    )

    assert result.json["score"] == 5
    assert len(requests) == 2


def test_does_not_retry_other_4xx() -> None:
    meter = CostMeter(InMemorySink())
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client, requests = make_client(handler, meter, sleep=delays.append)
    with pytest.raises(JudgeError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )

    assert len(requests) == 1
    assert delays == []


def test_exhausts_three_attempts() -> None:
    meter = CostMeter(InMemorySink())
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client, requests = make_client(handler, meter, sleep=delays.append)
    with pytest.raises(RetryExhaustedError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )

    assert len(requests) == 3
    assert delays == [0.5, 1.0]
    assert meter.events == []


def test_missing_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    meter = CostMeter(InMemorySink())
    client = JudgeClient(meter=meter)
    with pytest.raises(MissingAPIKeyError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )


def test_invalid_json_content_raises() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        lambda request: httpx.Response(200, json=success_body("not json")),
        meter,
    )
    with pytest.raises(ResponseFormatError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )
    assert len(meter.events) == 1
    assert meter.events[0].meta["parse_ok"] is False
    assert meter.events[0].in_tokens == 100
    assert meter.events[0].out_tokens == 20


def test_pydantic_validation_failure_still_meters() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        lambda request: httpx.Response(200, json=success_body('{"wrong": 1}')),
        meter,
    )
    with pytest.raises(ResponseFormatError):
        client.complete(
            system="s",
            user="u",
            response_schema=Verdict,
            call_site="judge.test",
        )
    assert len(meter.events) == 1
    assert meter.events[0].meta["parse_ok"] is True
    assert meter.events[0].meta["schema_checked"] is True
    assert meter.events[0].meta["schema_ok"] is False


def test_unpriced_model_fails_before_request() -> None:
    meter = CostMeter(InMemorySink())
    with pytest.raises(PricingError):
        JudgeClient(
            meter=meter,
            api_key="test-openrouter-key",
            model="unknown/model",
        )


def test_gemini_judge_model_is_rejected() -> None:
    meter = CostMeter(InMemorySink())
    with pytest.raises(ValueError):
        JudgeClient(
            meter=meter,
            api_key="test-openrouter-key",
            model="google/gemini-2.5-flash",
        )


def test_malformed_200_body_raises_typed_and_meters() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        lambda request: httpx.Response(200, json=["not", "an", "object"]),
        meter,
    )
    with pytest.raises(ResponseFormatError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )
    assert len(meter.events) == 1
    assert meter.events[0].meta["parse_ok"] is False
    assert meter.events[0].meta["usage_reported"] is False
    assert meter.events[0].shadow_cost_usd == 0.0


def test_malformed_choices_raises_typed() -> None:
    meter = CostMeter(InMemorySink())
    body = {"choices": "nope", "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    client, _ = make_client(lambda request: httpx.Response(200, json=body), meter)
    with pytest.raises(ResponseFormatError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )
    assert len(meter.events) == 1
    assert meter.events[0].in_tokens == 7
    assert meter.events[0].out_tokens == 3


def test_missing_usage_is_flagged() -> None:
    meter = CostMeter(InMemorySink())
    body = success_body()
    del body["usage"]
    client, _ = make_client(lambda request: httpx.Response(200, json=body), meter)
    client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="judge.test",
    )
    assert meter.events[0].meta["usage_reported"] is False
    assert meter.events[0].meta["usage_invalid"] is True
    assert meter.events[0].in_tokens == 0
    assert meter.events[0].out_tokens == 0


def test_negative_usage_is_zeroed_and_metered() -> None:
    meter = CostMeter(InMemorySink())
    body = success_body(prompt_tokens=-5)
    client, _ = make_client(lambda request: httpx.Response(200, json=body), meter)
    result = client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="judge.test",
    )
    assert result.json["score"] == 5
    assert meter.events[0].in_tokens == 0
    assert meter.events[0].out_tokens == 20
    assert meter.events[0].meta["usage_invalid"] is True


def test_pydantic_datetime_is_json_safe() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        lambda request: httpx.Response(
            200,
            json=success_body('{"when": "2026-10-23T12:00:00Z"}'),
        ),
        meter,
    )
    result = client.complete(
        system="s",
        user="u",
        response_schema=DatedVerdict,
        call_site="judge.test",
    )
    assert isinstance(result.json["when"], str)


def test_redirect_is_an_error() -> None:
    meter = CostMeter(InMemorySink())
    client, _ = make_client(
        lambda request: httpx.Response(307, headers={"Location": "/elsewhere"}),
        meter,
    )
    with pytest.raises(JudgeError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )
    assert meter.events == []


def test_max_retries_is_capped_at_three() -> None:
    meter = CostMeter(InMemorySink())
    client, requests = make_client(
        lambda request: httpx.Response(200, json=success_body()),
        meter,
    )
    with pytest.raises(ValueError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
            max_retries=4,
        )
    assert requests == []


def test_non_json_constructor_meta_fails_before_request() -> None:
    meter = CostMeter(InMemorySink())
    client, requests = make_client(
        lambda request: httpx.Response(200, json=success_body()),
        meter,
        meta={"bad": object()},
    )
    with pytest.raises(CostSinkError):
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )
    assert requests == []


def test_content_parts_are_joined() -> None:
    meter = CostMeter(InMemorySink())
    body = success_body()
    body["choices"][0]["message"]["content"] = [
        {"type": "text", "text": '{"score": 4,'},
        {"type": "text", "text": ' "reason": "fine"}'},
    ]
    client, _ = make_client(lambda request: httpx.Response(200, json=body), meter)
    result = client.complete(
        system="s",
        user="u",
        response_schema={"type": "object"},
        call_site="judge.test",
    )
    assert result.json == {"score": 4, "reason": "fine"}


def test_key_never_appears_in_exception(monkeypatch) -> None:
    key = "sk-or-v1-" + "Z" * 32
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    meter = CostMeter(InMemorySink())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad credential {key}")

    client, _ = make_client(handler, meter, api_key=key)
    with pytest.raises(JudgeError) as excinfo:
        client.complete(
            system="s",
            user="u",
            response_schema={"type": "object"},
            call_site="judge.test",
        )

    assert key not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)
