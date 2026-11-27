"""Lane 16: friendly ingestion (one-command add, idempotency, cost preview)."""

from __future__ import annotations

from .errors import (
    ExtractError,
    IngestConfigError,
    IngestError,
    IngestInputError,
)
from .estimate import CostEstimate, estimate_compile_cost
from .flow import ExtractedDocument, IngestOutcome, add_documents

__all__ = [
    "CostEstimate",
    "ExtractError",
    "ExtractedDocument",
    "IngestConfigError",
    "IngestError",
    "IngestInputError",
    "IngestOutcome",
    "add_documents",
    "estimate_compile_cost",
]
