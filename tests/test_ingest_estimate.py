"""Lane 16: pre-run cost estimate (pure, no DB, no provider)."""

from __future__ import annotations

import pytest

from wikimind.ingest import estimate_compile_cost
from wikimind.prices import GEMINI_MODEL, JUDGE_MODEL, price_for


def test_estimate_scales_with_units_and_claims() -> None:
    one = estimate_compile_cost(["Alpha joined Beta Labs in 2017."])
    two = estimate_compile_cost(["Alpha joined Beta Labs in 2017."] * 2)

    assert one.units == 1
    assert two.units == 2
    assert two.drafter_calls == one.drafter_calls * 2
    assert two.judge_calls == one.judge_calls * 2
    assert two.shadow_cost_usd == pytest.approx(one.shadow_cost_usd * 2, rel=1e-9)

    dense = estimate_compile_cost(["passage"], claims_per_unit=5)
    assert dense.judge_calls == 5
    assert dense.shadow_cost_usd > one.shadow_cost_usd / 2


def test_estimate_uses_pinned_prices_including_paid_twin_for_judge() -> None:
    estimate = estimate_compile_cost(["passage"])

    expected = price_for(GEMINI_MODEL).cost_usd(
        estimate.drafter_in_tokens, estimate.drafter_out_tokens
    ) + price_for(JUDGE_MODEL).cost_usd(estimate.judge_in_tokens, estimate.judge_out_tokens)
    assert estimate.shadow_cost_usd == pytest.approx(expected, rel=1e-9)
    # Ruling 11: a :free route is shadow-priced at its paid twin, never $0.
    assert estimate.judge_cost_usd > 0


def test_estimate_describe_exposes_assumptions() -> None:
    text = estimate_compile_cost(["passage"]).describe()

    assert "units to compile: 1" in text
    assert GEMINI_MODEL in text
    assert JUDGE_MODEL in text
    assert ":free route priced at its paid twin" in text
    assert "expected shadow cost:" in text
    assert "1 token ~ 4 chars" in text


def test_estimate_rejects_negative_claims() -> None:
    with pytest.raises(ValueError, match="claims_per_unit"):
        estimate_compile_cost(["passage"], claims_per_unit=-1)


def test_estimate_empty_passage_list_is_zero() -> None:
    estimate = estimate_compile_cost([])
    assert estimate.shadow_cost_usd == 0
    assert estimate.drafter_calls == 0
    assert estimate.judge_calls == 0
