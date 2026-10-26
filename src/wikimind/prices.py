"""Production list prices and pinned model identifiers.

All prices are USD per 1,000,000 tokens, verified 2026-10-23.

Gemini 2.5 Flash
    Source: Google Gemini API pricing (ai.google.dev/gemini-api/docs/pricing)
    and corroborating trackers refreshed 2026-10-11 / 2026-10-17:
    $0.30 input / $2.50 output per 1M text tokens.  Both the distill arm
    (l1.*) and the QA arm (qa.*) use this identical model, per the frozen
    model-stack decision.  The free tier has daily request limits; the cost
    meter always prices list price regardless of the free tier.

Judge
    Pinned at build time by checking the live OpenRouter model catalog
    (https://openrouter.ai/api/v1/models, fetched 2026-10-23).  The card's
    default ``deepseek/deepseek-chat-v3-0324:free`` is no longer listed, and
    the catalog contained no DeepSeek, Qwen, or Llama ``:free`` variant.
    Closest acceptable substitute: ``nvidia/nemotron-3-super-120b-a12b:free``
    -- free, NON-Gemini (120B total / 12B active MoE), 262K context, and it
    advertises structured-output support.  The judge is NEVER a Gemini model.
    This is outside Ruling 8's enumerated DeepSeek/Qwen/Llama families; it is
    recorded here for human confirmation.

    The judge endpoint itself costs $0.  Shadow cost prices its paid twin,
    ``nvidia/nemotron-3-super-120b-a12b``: $0.30 input / $2.40 output per 1M
    tokens (same OpenRouter catalog fetch).  Actual spend is recorded as $0
    in CostEvent.meta; the shadow figure is what the M4/M5 budget audit uses.

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


GEMINI_MODEL = "gemini-2.5-flash"
JUDGE_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
JUDGE_SHADOW_MODEL = "nvidia/nemotron-3-super-120b-a12b"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
FAKE_LLM_MODEL = "fake-llm"
FAKE_EMBED_MODEL = "fake-embedder"
EMBED_DIM = 384

PRICES_VERIFIED_ON = "2026-10-23"
PRICING_SOURCES = {
    GEMINI_MODEL: "https://ai.google.dev/gemini-api/docs/pricing",
    JUDGE_MODEL: "https://openrouter.ai/api/v1/models",
    JUDGE_SHADOW_MODEL: "https://openrouter.ai/api/v1/models",
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


GEMINI_2_5_FLASH_PRICE = ModelPrice(
    input_per_mtok=0.30,
    output_per_mtok=2.50,
)

JUDGE_SHADOW_PRICE = ModelPrice(
    input_per_mtok=0.30,
    output_per_mtok=2.40,
)

LOCAL_EMBED_PRICE = ModelPrice(input_per_mtok=0.0, output_per_mtok=0.0)

PRICES: Mapping[str, ModelPrice] = MappingProxyType(
    {
        GEMINI_MODEL: GEMINI_2_5_FLASH_PRICE,
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
