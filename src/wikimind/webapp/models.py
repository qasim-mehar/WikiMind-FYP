"""Small, database-free view models shared by the pages and the services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    """One document row as shown on the Documents page."""

    id: str
    title: str
    slug: str
    claim_count: int
    sha256: str
    source_uri: str
    parser: str


@dataclass(frozen=True, slots=True)
class ClaimDetail:
    """The claim behind a citation, plus its source document and trust status."""

    claim_id: str
    ref: str
    text: str
    anchor_quote: str
    document_slug: str
    document_title: str
    source_uri: str
    status: str


@dataclass(frozen=True, slots=True)
class CitationView:
    """A rendered citation: clickable short ref + the underlying claim/document."""

    claim_id: str
    ref: str
    document_slug: str
    text: str
    status: str
    source_uri: str


@dataclass(frozen=True, slots=True)
class AskView:
    """Everything the Ask page needs after one QA pipeline call."""

    question: str
    answer: str
    sufficient: bool
    citations: tuple[CitationView, ...]
    shadow_cost_usd: float
    retrieved_claims: int
    unverified_removed: int
    truncated: bool
    retrieval_note: str = ""


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Result of one compile invocation started from the Add page."""

    shadow_cost_usd: float
    claims_committed: int
    documents_registered: int
    summary: str


@dataclass(frozen=True, slots=True)
class JobProgress:
    """Immutable snapshot of an ingest job's visible stage log."""

    id: str
    state: str  # running | done | failed
    stages: tuple[str, ...]
    shadow_cost_usd: float = 0.0
    claims_committed: int = 0
    message: str = ""


@dataclass(frozen=True, slots=True)
class SelectableFile:
    """A local file the Add page can select instead of uploading bytes."""

    path: Path
    name: str
    size: int
