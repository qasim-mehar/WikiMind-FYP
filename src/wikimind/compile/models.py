"""Pydantic output models for the three compile stages.

Each model mirrors the JSON shape its pinned prompt mandates (see
``prompts.py``).  The LLM clients validate responses against these models;
a response that fails validation is retried once with the validation error
appended, then rejected and counted as a schema reject.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal[
    "concept",
    "person",
    "org",
    "system",
    "method",
    "metric",
    "event",
    "place",
    "other",
]
Confidence = Literal["high", "medium", "low"]
Disposition = Literal["new", "supersedes", "contradicts", "duplicate", "reject"]


class ProposedEntity(BaseModel):
    """One entity proposal from the analyzer or writer."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    type: EntityType = "other"


class ProposedRelation(BaseModel):
    """One proposed (subject, predicate, object) triple.

    ``object`` may be an entity name or a literal value; literal objects
    never become graph edges.
    """

    model_config = ConfigDict(extra="ignore")

    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)


class AnalyzedClaim(BaseModel):
    """One candidate claim extracted by ANALYZE."""

    model_config = ConfigDict(extra="ignore")

    claim_id_hint: str = Field(min_length=1)
    text: str = Field(min_length=1)
    support_quote: str = Field(min_length=1)
    entities: list[ProposedEntity] = Field(default_factory=list)
    relations: list[ProposedRelation] = Field(default_factory=list)
    confidence: Confidence = "medium"
    notes: str = ""


class AnalyzeOutput(BaseModel):
    """ANALYZE response: candidate claims for one passage."""

    model_config = ConfigDict(extra="ignore")

    claims: list[AnalyzedClaim] = Field(default_factory=list)


class WriteOutput(BaseModel):
    """WRITE response: final claim text plus an integration disposition."""

    model_config = ConfigDict(extra="ignore")

    disposition: Disposition
    target_claim_id: str | None = None
    text: str = Field(min_length=1)
    entities: list[ProposedEntity] = Field(default_factory=list)
    relations: list[ProposedRelation] = Field(default_factory=list)
    notes: str = ""


class JudgeOutput(BaseModel):
    """JUDGE response: entailment verdict and 0-5 score."""

    model_config = ConfigDict(extra="ignore")

    entailed: bool
    score: int = Field(ge=0, le=5)
    reason: str = ""
