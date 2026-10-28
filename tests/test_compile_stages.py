"""Keyless tests for the three compile stages and prompt-block builders.

Uses the Lane 2 ``FakeLLM``: no network, no keys, metered fake events.
Covers the retry-once-then-reject validation path, call-site wiring, prompt
composition (pinned prompt + untrusted input blocks), and pacing.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from wikimind.compile import ANALYZE_PROMPT, WRITE_PROMPT
from wikimind.compile.blocks import (
    candidate_block,
    channel_for_unit,
    claim_block,
    neighborhood_block,
    passage_block,
)
from wikimind.compile.graph import NeighborhoodClaim, Unit
from wikimind.compile.models import AnalyzedClaim
from wikimind.compile.stages import StageResult, inter_call_delay, run_analyze, run_judge, run_write
from wikimind.llm import CostMeter, FakeLLM, InMemorySink, Usage

PASSAGE_TEXT = "Alpha joined Beta Labs in 2017."
CHANNEL_ID = "c" * 64
PASSAGE_BLOCK = (
    f'<untrusted source="channel:{CHANNEL_ID}" kind="section">{PASSAGE_TEXT}</untrusted>'
)


def _unit() -> Unit:
    return Unit(
        document_id=UUID(int=2),
        document_sha256="d" * 64,
        channel_id=CHANNEL_ID,
        kind="section",
        ordinal=0,
        raw_text=PASSAGE_TEXT,
    )


GOOD_ANALYZE = {
    "claims": [
        {
            "claim_id_hint": "c1",
            "text": PASSAGE_TEXT,
            "support_quote": PASSAGE_TEXT,
            "entities": [
                {"name": "Alpha", "type": "person"},
                {"name": "Beta Labs", "type": "org"},
            ],
            "relations": [{"subject": "Alpha", "predicate": "joined", "object": "Beta Labs"}],
            "confidence": "high",
            "notes": "",
        }
    ]
}

GOOD_WRITE = {
    "disposition": "new",
    "target_claim_id": None,
    "text": PASSAGE_TEXT,
    "entities": [{"name": "Alpha", "type": "person"}],
    "relations": [],
    "notes": "",
}

GOOD_JUDGE = {"entailed": True, "score": 5, "reason": "verbatim quote in passage."}

BAD_PAYLOAD = {"claims": [{"wrong": "shape"}]}


def _candidate() -> AnalyzedClaim:
    return AnalyzedClaim.model_validate(GOOD_ANALYZE["claims"][0])


def test_run_analyze_success_and_call_site() -> None:
    fake = FakeLLM([GOOD_ANALYZE])
    result = run_analyze(fake, passage_block=PASSAGE_BLOCK, meta={"run": "r1"})

    assert isinstance(result, StageResult) and result.ok
    assert result.value is not None
    assert result.value.claims[0].claim_id_hint == "c1"
    call = fake.calls[0]
    assert call.call_site == "compile.analyze"
    assert call.meta == {"run": "r1"}
    # The pinned prompt is the message body; the passage is untrusted-tagged.
    assert call.user.startswith(ANALYZE_PROMPT)
    assert '<untrusted source="channel:' in call.user
    assert PASSAGE_TEXT in call.user


def test_run_analyze_retries_once_with_error_then_succeeds() -> None:
    fake = FakeLLM([BAD_PAYLOAD, GOOD_ANALYZE])
    result = run_analyze(fake, passage_block=PASSAGE_BLOCK)

    assert result.ok
    assert len(fake.calls) == 2
    assert "Your previous response was not valid" in fake.calls[1].user
    assert fake.calls[1].user.startswith(ANALYZE_PROMPT)


def test_run_analyze_schema_reject_after_one_retry() -> None:
    fake = FakeLLM([BAD_PAYLOAD, BAD_PAYLOAD])
    result = run_analyze(fake, passage_block=PASSAGE_BLOCK)

    assert result.value is None
    assert result.schema_rejected
    assert result.error is not None
    assert len(fake.calls) == 2  # exactly one retry, never a loop


def test_run_write_and_judge_call_sites_and_prompts() -> None:
    write_fake = FakeLLM([GOOD_WRITE])
    judge_fake = FakeLLM([GOOD_JUDGE])

    written = run_write(
        write_fake,
        candidate_block=candidate_block(_candidate()),
        neighborhood_block=neighborhood_block([]),
    )
    judged = run_judge(
        judge_fake,
        passage_block=PASSAGE_BLOCK,
        claim_block=claim_block(PASSAGE_TEXT),
    )

    assert written.ok and written.value is not None
    assert written.value.disposition == "new"
    assert write_fake.calls[0].call_site == "compile.write"
    assert write_fake.calls[0].user.startswith(WRITE_PROMPT)
    assert "<untrusted_candidate>" in write_fake.calls[0].user
    assert "<untrusted_neighborhood>" in write_fake.calls[0].user

    assert judged.ok and judged.value is not None
    assert judged.value.entailed is True
    assert judged.value.score == 5
    assert judge_fake.calls[0].call_site == "compile.judge"
    assert "<untrusted_claim>" in judge_fake.calls[0].user


def test_stage_delay_is_paced_via_injected_sleep() -> None:
    sleeps: list[float] = []
    fake = FakeLLM([GOOD_ANALYZE])
    run_analyze(fake, passage_block=PASSAGE_BLOCK, delay=2.5, sleep=sleeps.append)
    assert sleeps == [2.5]

    sleeps.clear()
    fake = FakeLLM([GOOD_ANALYZE])
    run_analyze(fake, passage_block=PASSAGE_BLOCK, delay=0.0, sleep=sleeps.append)
    assert sleeps == []


def test_fake_meter_records_zero_cost_events() -> None:
    sink = InMemorySink()
    meter = CostMeter(sink)
    fake = FakeLLM([GOOD_ANALYZE], meter=meter, usage=Usage(100, 20))
    run_analyze(fake, passage_block=PASSAGE_BLOCK)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.call_site == "compile.analyze"
    assert event.in_tokens == 100
    assert event.out_tokens == 20
    assert event.shadow_cost_usd == 0.0  # fake-llm prices at zero


def test_inter_call_delay_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WIKIMIND_COMPILE_DELAY", raising=False)
    assert inter_call_delay() == 0.0
    monkeypatch.setenv("WIKIMIND_COMPILE_DELAY", "2.5")
    assert inter_call_delay() == 2.5
    monkeypatch.setenv("WIKIMIND_COMPILE_DELAY", "bogus")
    assert inter_call_delay() == 0.0
    monkeypatch.setenv("WIKIMIND_COMPILE_DELAY", "-3")
    assert inter_call_delay() == 0.0


def test_blocks_escape_and_normalize() -> None:
    channel = channel_for_unit(_unit())
    passage = passage_block(channel)
    assert passage.startswith('<untrusted source="channel:')
    assert f"{PASSAGE_TEXT}</untrusted>" in passage

    block = candidate_block(
        AnalyzedClaim.model_validate(
            {
                "claim_id_hint": "c<1>",
                "text": "Alpha <joined> & worked",
                "support_quote": "Alpha",
                "confidence": "high",
            }
        )
    )
    assert "claim_id_hint: c&lt;1&gt;" in block
    assert "Alpha &lt;joined&gt; &amp; worked" in block

    other = UUID(int=9)
    hood = neighborhood_block(
        [NeighborhoodClaim(id=other, text="existing  claim\ntext", entities=("alpha",))]
    )
    assert f"claim:{other} entities=alpha :: existing claim text" in hood

    claim = claim_block("multi\nline   claim")
    assert claim.startswith("<untrusted_claim>")
    assert claim.endswith("</untrusted_claim>")
    assert "multi line claim" in claim


def test_stage_result_defaults() -> None:
    result = StageResult(value=None)
    assert not result.ok
    assert not result.schema_rejected
