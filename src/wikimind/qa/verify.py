"""Machine-verified citations (Ruling 21, F7/F10).

The answer composer is told to cite like ``[c-1234]`` while the system hands it
short refs of the form ``c-<12 hex>``. A cited id is trusted only if it maps
unambiguously to a ref that was actually supplied to the model, and, when the
model also returned a ``document_slug`` for it, that slug matches the claim's
canonical document slug. Verification is pure post-processing: the frozen
prompt and the answer text are untouched.

Only claim-ref-shaped candidates (``c-`` plus at least 4 hex characters) count.
Ordinary bracketed prose such as ``[1]`` or ``[Table 1]`` is ignored entirely —
never verified, never reported as an unverified removal.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .prompts import AnswerCitation, AnswerPayload, PromptClaim

# The frozen prompt's own example is [c-1234]: "c-" + 4 hex is the minimum.
_REF_SHAPE = r"c-[0-9a-fA-F]{4,}"
_BRACKET_RE = re.compile(rf"\[({_REF_SHAPE})\]")
_SHAPE_RE = re.compile(rf"^{_REF_SHAPE}$")


def _normalize(ref: str) -> str:
    """Normalize a cited id for tolerant matching against provided refs."""

    return ref.strip().lower().removeprefix("c-")


def is_citation_candidate(value: str) -> bool:
    """Whether a bracket contents / array id has the canonical claim-ref shape."""

    return _SHAPE_RE.fullmatch(value.strip()) is not None


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
    """Shaped ids cited in the answer text or array, in first-seen order.

    Unshaped bracket contents and array ids are ignored completely (F7).
    """

    ids: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        candidate = value.strip()
        key = _normalize(candidate)
        if key not in seen and is_citation_candidate(candidate):
            seen.add(key)
            ids.append(candidate)

    for match in _BRACKET_RE.finditer(answer):
        add(match.group(1))
    for citation in citations:
        add(citation.claim_id)
    return ids


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Citations that survived verification, the removal count, and the prose.

    ``answer`` is the printed answer text with unverified claim-ref brackets
    stripped (Ruling 24). The sentence itself is left intact; only the
    bracketed token and its immediately preceding whitespace are removed.
    """

    citations: tuple[AnswerCitation, ...]
    unverified_removed: int
    answer: str


def strip_unverified_refs(answer: str, unverified: Sequence[str]) -> str:
    """Remove only the bracket tokens for refs that failed verification.

    Unshaped bracket prose (``[1]``, ``[Table 1]``) is never touched. A shaped
    ref is matched as a whole bracket, so ``[c-abcd]`` goes but ``[c-abcdx]``
    does not, and the surrounding sentence stays byte-identical otherwise.
    """

    cleaned = answer
    for ref in unverified:
        token = ref.strip()
        key = _normalize(token)
        if not is_citation_candidate(token):
            continue
        # Match the exact bracket token case-insensitively on the hex part.
        pattern = re.compile(
            r"[ \t]*\[" + re.escape("c-") + re.escape(key) + r"\](?=\W|$)", re.IGNORECASE
        )
        cleaned = pattern.sub("", cleaned)
    # Collapse whitespace introduced by removal, keep punctuation attached.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return cleaned.strip()


def verify_citations(
    payload: AnswerPayload,
    provided: Sequence[PromptClaim],
    *,
    claim_ids: Mapping[str, str] | None = None,
) -> VerificationResult:
    """Drop citations that cannot be mapped or whose document slug disagrees.

    Ruling 22: ``citations[].claim_id`` in the structured (JSON) contract is
    the canonical full UUID. The model only ever sees short refs (the frozen
    prompt is unchanged), so ``claim_ids`` maps a provided short ref to its
    full UUID. Prose keeps the short ref; only the array is canonicalized.
    When no mapping is supplied (direct unit tests), the short ref is kept.
    """

    # F10a: group every array entry by normalized key instead of letting the
    # last one shadow the rest. Agreement is harmless; disagreement is fatal.
    array_entries: dict[str, list[AnswerCitation]] = defaultdict(list)
    for citation in payload.citations:
        array_entries[_normalize(citation.claim_id)].append(citation)

    verified: dict[str, AnswerCitation] = {}
    unverified: list[str] = []
    removed = 0

    for cited in extract_cited_ids(payload.answer, payload.citations):
        claim = map_cited_id(cited, provided)
        if claim is None:
            removed += 1
            unverified.append(cited)
            continue
        entries = array_entries.get(_normalize(cited), [])
        slugs = {entry.document_slug for entry in entries}
        if len(slugs) > 1:
            removed += 1
            unverified.append(cited)
            continue
        if slugs and next(iter(slugs)) != claim.document_slug:
            removed += 1
            unverified.append(cited)
            continue
        canonical = claim_ids.get(claim.ref, claim.ref) if claim_ids else claim.ref
        verified.setdefault(
            canonical,
            AnswerCitation(claim_id=canonical, document_slug=claim.document_slug),
        )

    return VerificationResult(
        tuple(verified.values()),
        removed,
        strip_unverified_refs(payload.answer, unverified),
    )
