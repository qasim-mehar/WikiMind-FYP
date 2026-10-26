"""Typed errors for the model access layer.

Every message passes through the redactor so an API key can never surface in
a traceback, log line, or CLI error.
"""

from __future__ import annotations

from ..prices import PricingError
from .secrets import redact_secrets

__all__ = [
    "CostSinkError",
    "EmbeddingError",
    "GeminiError",
    "JudgeError",
    "MissingAPIKeyError",
    "PricingError",
    "ProviderError",
    "ResponseFormatError",
    "RetryExhaustedError",
    "WikiMindLLMError",
]


class WikiMindLLMError(Exception):
    def __init__(self, message: str = "") -> None:
        super().__init__(redact_secrets(message))


class MissingAPIKeyError(WikiMindLLMError):
    pass


class ProviderError(WikiMindLLMError):
    pass


class GeminiError(ProviderError):
    pass


class JudgeError(ProviderError):
    pass


class RetryExhaustedError(ProviderError):
    pass


class ResponseFormatError(ProviderError):
    pass


class EmbeddingError(WikiMindLLMError):
    pass


class CostSinkError(WikiMindLLMError):
    pass
