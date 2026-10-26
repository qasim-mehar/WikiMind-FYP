"""Shared types and protocols for the model access layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel

JsonSchema: TypeAlias = dict[str, Any]
ResponseSchema: TypeAlias = JsonSchema | type[BaseModel] | None


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage reported by a provider for one successful call."""

    in_tokens: int = 0
    out_tokens: int = 0

    def __post_init__(self) -> None:
        if self.in_tokens < 0 or self.out_tokens < 0:
            raise ValueError("token counts must be non-negative")

    @property
    def total_tokens(self) -> int:
        return self.in_tokens + self.out_tokens


@dataclass(frozen=True, slots=True)
class LLMResult:
    """A parsed JSON response plus the provider's token usage."""

    json: dict[str, Any]
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class LLMClient(Protocol):
    """Metered JSON-mode LLM interface.

    ``max_retries`` is the maximum number of total attempts (initial call plus
    retries), not the number of retries after the initial call.
    ``meta`` is an additive optional extension: A/B arm tags and model
    versions are carried through to the CostEvent meta dict.
    """

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: ResponseSchema,
        call_site: str,
        max_retries: int = 3,
        meta: dict[str, Any] | None = None,
    ) -> LLMResult: ...


@runtime_checkable
class Embedder(Protocol):
    """Local embedder interface; implementations must return EMBED_DIM floats."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...
