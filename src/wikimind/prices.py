"""Production list prices and pinned model identifiers.

All prices are USD per 1,000,000 tokens, verified 2026-10-27.

Gemini 3.1 Flash-Lite (drafter)
    Source: Google Gemini API pricing (ai.google.dev/gemini-api/docs/pricing),
    retrieved 2026-10-27: $0.25 input / $1.50 output per 1M text tokens
    (standard paid tier).  Pinned by V0-NOTES Ruling 12 after the live
    pre-flight smoke proved ``gemini-2.5-flash`` is permanently retired
    upstream (404 for new users) and the vendor-named successor
    ``gemini-3.8-flash`` was unusable (503 x3, 504 at 180 s).  A model pin
    requires a working live inference path through our own client, proven
    by live smoke; catalog listing is not liveness.  Both the distill arm
    (l1.*) and the QA arm (qa.*) use this identical model, per the frozen
    model-stack decision.  The free tier has daily request limits; the cost
    meter always prices list price regardless of the free tier.

Judge
    Pinned at build time by checking the live OpenRouter model catalog.
    The Ruling 8-era ``deepseek/deepseek-chat-v3-0324:free`` was dropped
    from the catalog (re-pin to Nemotron recorded in Ruling 10); the live
    pre-flight smoke of 2026-10-27 then showed that pin's free route is
    per-account dead (404 from the Nvidia provider while the catalog still
    lists the slug).  Ruling 13 re-pins the judge to
    ``nvidia/nemotron-3-ultra-550b-a55b:free`` (working via Nvidia,
    ~2 s).  Binding constraints, unchanged since Ruling 10: never a Gemini
    model, :free tier, competent at entailment checking.

    The judge endpoint itself costs $0.  Per Ruling 11, free routes are
    priced at the pinned paid twin, never at zero: shadow cost uses
    ``nvidia/nemotron-3-ultra-550b-a55b`` at $0.60 input / $2.40 output
    per 1M tokens (OpenRouter model page, probed 2026-10-27).  Actual
    spend is recorded as $0 in CostEvent.meta; the shadow figure is what
    the budget audit uses.

Embedding
    BAAI/bge-small-en-v1.5 runs locally (CPU), so its list price is $0.
    Calls are still metered so every model invocation is visible in the audit.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field


class PricingError(ValueError):
    """Raised when a model has no audited list price."""


GEMINI_MODEL = "gemini-3.1-flash-lite"
JUDGE_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
JUDGE_SHADOW_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
FAKE_LLM_MODEL = "fake-llm"
FAKE_EMBED_MODEL = "fake-embedder"
EMBED_DIM = 384

PRICES_VERIFIED_ON = "2026-10-27"
PRICING_SOURCES = {
    GEMINI_MODEL: (
        "https://ai.google.dev/gemini-api/docs/pricing (retrieved 2026-10-27)"
    ),
    JUDGE_MODEL: (
        "https://openrouter.ai/model-catalog (probed 2026-10-27; endpoint :free)"
    ),
    JUDGE_SHADOW_MODEL: (
        "OpenRouter model page for nvidia/nemotron-3-ultra-550b-a55b "
        "(probed 2026-10-27)"
    ),
}


class ModelPrice(BaseModel):
    """List price for one model, in USD per 1M tokens."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    currency: str = "USD"

    def cost_usd(self, in_tokens: int, out_tokens: int) -> float:
        if in_tokens < 0 or out_tokens < 0:
            raise ValueError("token counts must be non-negative")
        return round(
            (in_tokens * self.input_per_mtok + out_tokens * self.output_per_mtok)
            / 1_000_000,
            12,
        )


GEMINI_3_1_FLASH_LITE_PRICE = ModelPrice(
    input_per_mtok=0.25,
    output_per_mtok=1.50,
)

JUDGE_SHADOW_PRICE = ModelPrice(
    input_per_mtok=0.60,
    output_per_mtok=2.40,
)

LOCAL_EMBED_PRICE = ModelPrice(input_per_mtok=0.0, output_per_mtok=0.0)

PRICES: Mapping[str, ModelPrice] = MappingProxyType(
    {
        GEMINI_MODEL: GEMINI_3_1_FLASH_LITE_PRICE,
        JUDGE_MODEL: JUDGE_SHADOW_PRICE,
        JUDGE_SHADOW_MODEL: JUDGE_SHADOW_PRICE,
        EMBED_MODEL: LOCAL_EMBED_PRICE,
        FAKE_LLM_MODEL: LOCAL_EMBED_PRICE,
        FAKE_EMBED_MODEL: LOCAL_EMBED_PRICE,
    }
)


def price_for(model: str) -> ModelPrice:
    """Return the audited list price for a model, or raise PricingError."""

    try:
        return PRICES[model]
    except KeyError as exc:
        raise PricingError(
            f"no audited price for model {model!r}; refusing to emit an unmetered call"
        ) from exc
