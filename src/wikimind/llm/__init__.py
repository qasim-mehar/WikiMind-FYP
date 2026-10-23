"""Model access layer: protocols, metered clients, embedders, cost meter."""

from __future__ import annotations

from ..prices import (
    EMBED_DIM,
    EMBED_MODEL,
    GEMINI_MODEL,
    JUDGE_MODEL,
    JUDGE_SHADOW_MODEL,
    ModelPrice,
    price_for,
)
from .cost import (
    CALL_SITE_PREFIXES,
    CostAggregate,
    CostEvent,
    CostMeter,
    CostSink,
    DbCostSink,
    InMemorySink,
    JsonlCostSink,
    aggregate_costs,
    default_costs_path,
    format_cost_report,
    format_usd,
    read_cost_events,
    validate_call_site,
)
from .embedder import FakeEmbedder, SentenceTransformerEmbedder
from .errors import (
    CostSinkError,
    EmbeddingError,
    GeminiError,
    JudgeError,
    MissingAPIKeyError,
    PricingError,
    ProviderError,
    ResponseFormatError,
    RetryExhaustedError,
    WikiMindLLMError,
)
from .fakes import FakeCall, FakeLLM
from .gemini import GeminiClient
from .judge import JudgeClient
from .protocols import Embedder, LLMClient, LLMResult, ResponseSchema, Usage

__all__ = [
    "CALL_SITE_PREFIXES",
    "EMBED_DIM",
    "EMBED_MODEL",
    "GEMINI_MODEL",
    "JUDGE_MODEL",
    "JUDGE_SHADOW_MODEL",
    "CostAggregate",
    "CostEvent",
    "CostMeter",
    "CostSink",
    "CostSinkError",
    "DbCostSink",
    "Embedder",
    "EmbeddingError",
    "FakeCall",
    "FakeEmbedder",
    "FakeLLM",
    "GeminiClient",
    "GeminiError",
    "InMemorySink",
    "JsonlCostSink",
    "JudgeClient",
    "JudgeError",
    "LLMClient",
    "LLMResult",
    "MissingAPIKeyError",
    "ModelPrice",
    "PricingError",
    "ProviderError",
    "ResponseFormatError",
    "ResponseSchema",
    "RetryExhaustedError",
    "SentenceTransformerEmbedder",
    "Usage",
    "WikiMindLLMError",
    "aggregate_costs",
    "default_costs_path",
    "format_cost_report",
    "format_usd",
    "price_for",
    "read_cost_events",
    "validate_call_site",
]
