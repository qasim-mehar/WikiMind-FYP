"""Machine-verified citations (Ruling 21).

The answer composer is told to cite like ``[c-1234]`` while the system hands it
short refs of the form ``c-<12 hex>``. A cited id is trusted only if it maps
unambiguously to a ref that was actually supplied to the model, and, when the
model also returned a ``document_slug`` for it, that slug matches the claim's
canonical document slug. Verification is pure post-processing: the frozen
prompt and the answer text are untouched.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .prompts import AnswerCitation, AnswerPayload, PromptClaim

_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")


def _normalize(ref: str) -> str:
    """Normalize a cited id for tolerant matching against provided refs."""

    return ref.strip().lower().removeprefix("c-")


def map_cited_id(cited: str, provided: Sequence[PromptClaim]) -> PromptClaim | None:
    """Map a cited id to a provided claim by exact match, then unique prefix.

    ``[c-1234]`` (the example in the frozen prompt) maps to ``c-123456781234``
    when that prefix is unique among the supplied refs. Ambiguous or unknown
    ids never map.
    """

    key = _normalize(cited)
    if not key:
        return None
    for claim in provided:
        if _normalize(claim.ref) == key:
            return claim
    matches = [claim for claim in provided if _normalize(claim.ref).startswith(key)]
    return matches[0] if len(matches) == 1 else None


def extract_cited_ids(answer: str, citations: Sequence[AnswerCitation]) -> list[str]:
    """Every id cited in the answer text or the citations array, in first-seen order."""

    ids: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        candidate = value.strip()
        key = _normalize(candidate)
        if candidate and key not in seen:
            seen.add(key)
            ids.append(candidate)

    for match in _BRACKET_RE.finditer(answer):
        add(match.group(1))
    for citation in citations:
        add(citation.claim_id)
    return ids


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Citations that survived verification, plus the removal count."""

    citations: tuple[AnswerCitation, ...]
    unverified_removed: int


def verify_citations(payload: AnswerPayload, provided: Sequence[PromptClaim]) -> VerificationResult:
    """Drop citations that cannot be mapped or whose document slug disagrees."""

    array_by_key = {_normalize(c.claim_id): c for c in payload.citations}
    verified: dict[str, AnswerCitation] = {}
    removed = 0

    for cited in extract_cited_ids(payload.answer, payload.citations):
        claim = map_cited_id(cited, provided)
        if claim is None:
            removed += 1
            continue
        returned = array_by_key.get(_normalize(cited))
        if returned is not None and returned.document_slug != claim.document_slug:
            removed += 1
            continue
        verified.setdefault(
            claim.ref,
            AnswerCitation(claim_id=claim.ref, document_slug=claim.document_slug),
        )

    return VerificationResult(tuple(verified.values()), removed)
