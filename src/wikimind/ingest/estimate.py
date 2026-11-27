"""Pre-run shadow-cost estimate for a compile pass.

Pure and offline: no database, no provider calls.  The estimate exists so a
user can see the *expected* bill before authorising distillation.  It is
deliberately conservative — it assumes every extracted candidate reaches both
WRITE and JUDGE — and states its assumptions in :meth:`CostEstimate.describe`.

Prices come from the single source of truth (``wikimind.prices``); this module
never invents a model or a price.  The judge is a ``:free`` route but is priced
at its paid twin per Ruling 11, exactly as the real meter does.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..compile.prompts import ANALYZE_PROMPT, JUDGE_PROMPT, WRITE_PROMPT
from ..prices import GEMINI_MODEL, JUDGE_MODEL, price_for

# Model-agnostic sizing assumptions.  These are *estimator inputs*, not model
# pins or prices; changing them changes only the preview, never the meter.
CHARS_PER_TOKEN = 4.0
SCHEMA_OVERHEAD_TOKENS = 16
CHANNEL_BUDGET_CHARS = 60_000  # compile_project(channel_budget) default
NEIGHBORHOOD_CHARS = 1_500
CANDIDATE_CHARS = 400
FINAL_CLAIM_CHARS = 400
ANALYZE_OUT_TOKENS = 140
WRITE_OUT_TOKENS = 100
JUDGE_OUT_TOKENS = 32
DEFAULT_CLAIMS_PER_UNIT = 3  # corpus average is ~2.3; round up for safety


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """A conservative, itemised shadow-cost preview for one compile pass."""

    units: int
    claims_per_unit: int
    drafter_calls: int
    judge_calls: int
    drafter_in_tokens: int
    drafter_out_tokens: int
    judge_in_tokens: int
    judge_out_tokens: int
    drafter_cost_usd: float
    judge_cost_usd: float

    @property
    def shadow_cost_usd(self) -> float:
        return round(self.drafter_cost_usd + self.judge_cost_usd, 12)

    def describe(self) -> str:
        """Human-readable preview with its assumptions made explicit."""

        drafter_line = (
            f"  drafter: {self.drafter_calls} calls, "
            f"~{self.drafter_in_tokens} in / {self.drafter_out_tokens} out tokens "
            f"({GEMINI_MODEL})"
        )
        judge_line = (
            f"  judge:   {self.judge_calls} calls, "
            f"~{self.judge_in_tokens} in / {self.judge_out_tokens} out tokens "
            f"({JUDGE_MODEL}, :free route priced at its paid twin)"
        )
        basis_line = (
            "  basis: 1 token ~ 4 chars; passages capped at "
            f"{CHANNEL_BUDGET_CHARS:,} chars; assumes every candidate "
            "reaches WRITE and JUDGE. Actual cost depends on extraction "
            "and judge-reject rates; the final figure is metered."
        )
        return "\n".join(
            [
                f"  units to compile: {self.units} (assumed {self.claims_per_unit} claims/unit)",
                drafter_line,
                judge_line,
                f"  expected shadow cost: <= ${self.shadow_cost_usd:.12f}",
                basis_line,
            ]
        )


def _tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN) + SCHEMA_OVERHEAD_TOKENS


def estimate_compile_cost(
    passage_texts: Sequence[str],
    *,
    claims_per_unit: int = DEFAULT_CLAIMS_PER_UNIT,
) -> CostEstimate:
    """Estimate shadow cost for ``passage_texts`` (one per pending unit).

    ``claims_per_unit`` is the only tunable assumption; it can be supplied by
    the caller when a corpus is known to be denser or sparser than average.
    Negative counts are rejected so a preview can never be misleading.
    """

    if claims_per_unit < 0:
        raise ValueError("claims_per_unit must be non-negative")

    analyze_in = _tokens(len(ANALYZE_PROMPT) + CHANNEL_BUDGET_CHARS)
    write_in = _tokens(len(WRITE_PROMPT) + CANDIDATE_CHARS + NEIGHBORHOOD_CHARS)
    judge_prompt_tokens = _tokens(len(JUDGE_PROMPT) + FINAL_CLAIM_CHARS)

    units = len(passage_texts)
    drafter_calls = units * (1 + claims_per_unit)
    judge_calls = units * claims_per_unit

    drafter_in = units * analyze_in + judge_calls * write_in
    drafter_out = units * ANALYZE_OUT_TOKENS + judge_calls * WRITE_OUT_TOKENS
    # The judge sees one passage + claim per candidate, not one per unit.
    judge_in = sum(
        claims_per_unit * (_tokens(min(len(text), CHANNEL_BUDGET_CHARS)) + judge_prompt_tokens)
        for text in passage_texts
    )
    judge_out = judge_calls * JUDGE_OUT_TOKENS

    drafter_price = price_for(GEMINI_MODEL)
    judge_price = price_for(JUDGE_MODEL)
    return CostEstimate(
        units=units,
        claims_per_unit=claims_per_unit,
        drafter_calls=drafter_calls,
        judge_calls=judge_calls,
        drafter_in_tokens=drafter_in,
        drafter_out_tokens=drafter_out,
        judge_in_tokens=judge_in,
        judge_out_tokens=judge_out,
        drafter_cost_usd=drafter_price.cost_usd(drafter_in, drafter_out),
        judge_cost_usd=judge_price.cost_usd(judge_in, judge_out),
    )


__all__ = ["DEFAULT_CLAIMS_PER_UNIT", "CostEstimate", "estimate_compile_cost"]
