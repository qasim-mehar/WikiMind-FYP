"""Gate C / Ruling 30: hard max-output-token cap on generation calls.

All clients here are fakes; no network and no LLM spend.  A cap breach must be
a failed attempt (retry + metered log event), never a returned/truncated answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from wikimind.llm import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_ENV,
    CostMeter,
    FakeEmbedder,
    GeminiClient,
    InMemorySink,
    JudgeClient,
    RetryExhaustedError,
    normalize_output_cap,
)
from wikimind.llm.errors import OutputCapExceededError
from wikimind.llm.guard import enforce_output_cap
from wikimind.prices import GEMINI_MODEL, JUDGE_MODEL

TRUNCATED_MARKER = "TRUNCATED_ANSWER_MUST_NOT_SURFACE"
RUNAWAY_TOKENS = 65_520


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def test_default_cap_is_4096() -> None:
    assert DEFAULT_MAX_OUTPUT_TOKENS == 4096
    from wikimind.llm import max_output_tokens_from_env

    assert max_output_tokens_from_env({}) == 4096
    assert max_output_tokens_from_env({MAX_OUTPUT_TOKENS_ENV: ""}) == 4096


def test_cap_is_configurable_via_existing_env_mechanism(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAX_OUTPUT_TOKENS_ENV, "128")
    meter = CostMeter(InMemorySink())
    client, _ = make_gemini([FakeResponse()], meter)
    assert client.output_token_cap == 128


def test_cap_is_configurable_via_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"{MAX_OUTPUT_TOKENS_ENV}=77\n", encoding="utf-8")
    monkeypatch.delenv(MAX_OUTPUT_TOKENS_ENV, raising=False)
    assert normalize_output_cap(None) == 77


def test_invalid_caps_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAX_OUTPUT_TOKENS_ENV, "0")
    with pytest.raises(ValueError):
        normalize_output_cap(None)
    with pytest.raises(ValueError):
        normalize_output_cap(-1)
    with pytest.raises(ValueError):
        normalize_output_cap(True)


def test_enforce_output_cap_strictly_greater_than_cap() -> None:
    enforce_output_cap(out_tokens=100, cap=100, call_site="qa.test", model=GEMINI_MODEL)
    with pytest.raises(OutputCapExceededError):
        enforce_output_cap(out_tokens=101, cap=100, call_site="qa.test", model=GEMINI_MODEL)


# --------------------------------------------------------------------------- #
# Gemini (drafter / answer path)
# --------------------------------------------------------------------------- #


@dataclass
class FakeUsage:
    prompt_token_count: int = 10
    candidates_token_count: int = 20
    thoughts_token_count: int = 5


class FakeResponse:
    def __init__(self, text: str = '{"ok": true}', *, candidates: int = 20, thoughts: int = 5):
        self.text = text
        self.parsed = None
        self.usage_metadata = FakeUsage(
            candidates_token_count=candidates, thoughts_token_count=thoughts
        )
        self.model_version = "gemini-3.1-flash-lite-001"
        self.response_id = "resp-1"


def make_gemini(outcomes: list[Any], meter: CostMeter, **kwargs: Any):
    class FakeModels:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def generate_content(self, **call_kwargs: Any) -> Any:
            self.calls.append(call_kwargs)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    models = FakeModels()

    class FakeGenaiClient:
        def __init__(self) -> None:
            self.models = models

    client = GeminiClient(meter=meter, client=FakeGenaiClient(), **kwargs)
    return client, models


def test_gemini_cap_hit_retries_and_discards_truncated_text() -> None:
    meter = CostMeter(InMemorySink())
    oversized = FakeResponse(
        text=f'{{"answer": "{TRUNCATED_MARKER}"}}', candidates=5_000, thoughts=0
    )
    valid = FakeResponse(text='{"answer": "valid"}', candidates=20, thoughts=0)
    delays: list[float] = []
    client, models = make_gemini(
        [oversized, valid], meter, output_token_cap=100, sleep=delays.append
    )

    result = client.complete(
        system="s", user="u", response_schema={"type": "object"}, call_site="qa.answer"
    )

    assert result.json == {"answer": "valid"}
    assert TRUNCATED_MARKER not in str(result.json)
    assert len(models.calls) == 2
    assert delays == [0.5]
    assert len(meter.events) == 2
    hit, accepted = meter.events
    assert hit.out_tokens == 5_000
    assert hit.meta["output_cap_exceeded"] is True
    assert hit.meta["output_cap"] == 100
    assert "output_cap_exceeded" not in accepted.meta
    # Provider-side truncation is never used: no silent truncation path.
    assert getattr(models.calls[0]["config"], "max_output_tokens", None) is None


def test_gemini_cap_hit_exhausts_and_never_returns_truncated_text() -> None:
    meter = CostMeter(InMemorySink())
    runaway = FakeResponse(
        text=f'{{"answer": "{TRUNCATED_MARKER}"}}', candidates=RUNAWAY_TOKENS, thoughts=0
    )
    client, models = make_gemini([runaway] * 3, meter, output_token_cap=100, sleep=lambda _: None)

    with pytest.raises(RetryExhaustedError) as excinfo:
        client.complete(
            system="s", user="u", response_schema={"type": "object"}, call_site="qa.answer"
        )

    assert TRUNCATED_MARKER not in str(excinfo.value)
    assert len(models.calls) == 3
    assert len(meter.events) == 3
    assert all(event.meta.get("output_cap_exceeded") is True for event in meter.events)
    assert all(event.out_tokens == RUNAWAY_TOKENS for event in meter.events)


def test_gemini_exactly_at_cap_is_accepted() -> None:
    meter = CostMeter(InMemorySink())
    client, models = make_gemini(
        [FakeResponse(candidates=100, thoughts=0)], meter, output_token_cap=100
    )
    result = client.complete(
        system="s", user="u", response_schema={"type": "object"}, call_site="qa.answer"
    )
    assert result.json == {"ok": True}
    assert len(models.calls) == 1
    assert len(meter.events) == 1
    assert "output_cap_exceeded" not in meter.events[0].meta


# --------------------------------------------------------------------------- #
# Judge (OpenRouter entailment generation)
# --------------------------------------------------------------------------- #


def judge_body(completion_tokens: int = 20, content: str = '{"ok": true}') -> dict[str, Any]:
    return {
        "id": "gen-1",
        "model": JUDGE_MODEL,
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": completion_tokens},
    }


def make_judge(bodies: list[Any], meter: CostMeter, **kwargs: Any):
    requests: list[httpx.Request] = []
    remaining = list(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = remaining.pop(0)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, json=body)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = JudgeClient(
        meter=meter, api_key="test-openrouter-key", http_client=http_client, **kwargs
    )
    return client, requests


def test_judge_cap_hit_retries_and_discards_truncated_text() -> None:
    meter = CostMeter(InMemorySink())
    oversized = judge_body(5_000, f'{{"ok": "{TRUNCATED_MARKER}"}}')
    client, requests = make_judge(
        [oversized, judge_body()], meter, output_token_cap=100, sleep=lambda _: None
    )

    result = client.complete(
        system="s", user="u", response_schema={"type": "object"}, call_site="judge.test"
    )

    assert result.json == {"ok": True}
    assert TRUNCATED_MARKER not in str(result.json)
    assert len(requests) == 2
    assert len(meter.events) == 2
    assert meter.events[0].out_tokens == 5_000
    assert meter.events[0].meta["output_cap_exceeded"] is True
    assert meter.events[0].meta["output_cap"] == 100
    assert "output_cap_exceeded" not in meter.events[1].meta
    assert "max_tokens" not in json.loads(requests[0].content)


def test_judge_cap_hit_exhausts_and_never_returns_truncated_text() -> None:
    meter = CostMeter(InMemorySink())
    runaway = judge_body(RUNAWAY_TOKENS, f'{{"ok": "{TRUNCATED_MARKER}"}}')
    client, requests = make_judge([runaway] * 3, meter, output_token_cap=100, sleep=lambda _: None)

    with pytest.raises(RetryExhaustedError) as excinfo:
        client.complete(
            system="s", user="u", response_schema={"type": "object"}, call_site="judge.test"
        )

    assert TRUNCATED_MARKER not in str(excinfo.value)
    assert len(requests) == 3
    assert len(meter.events) == 3
    assert all(event.meta.get("output_cap_exceeded") is True for event in meter.events)
    assert all(event.out_tokens == RUNAWAY_TOKENS for event in meter.events)


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #


def test_embeddings_are_out_of_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAX_OUTPUT_TOKENS_ENV, "1")
    meter = CostMeter(InMemorySink())
    embedder = FakeEmbedder(meter=meter)
    vectors = embedder.embed(["a text longer than the cap"])
    assert len(vectors) == 1
    assert len(meter.events) == 1
    assert "output_cap_exceeded" not in meter.events[0].meta
