"""User-message input blocks for the three pinned stage prompts.

The pinned prompts (``prompts.py``) are never edited; this module renders
only the input sections that follow each prompt's trailing "follows." line.
Channel text enters a prompt exclusively through the sanctioned Lane 1
helper (D4); candidate, neighborhood, and claim text are distilled data and
are wrapped in untrusted blocks with the same escaping (D8).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..parse.models import Channel, ChannelKind
from ..parse.prompt import channels_for_prompt
from .graph import NeighborhoodClaim
from .ids import normalize_claim_text
from .models import AnalyzedClaim

DEFAULT_CHANNEL_BUDGET = 60_000


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def passage_block(channel: Channel, char_budget: int = DEFAULT_CHANNEL_BUDGET) -> str:
    """The sanctioned tagged block for one channel unit."""

    return channels_for_prompt([channel], char_budget)


def channel_for_unit(unit: Any) -> Channel:
    """Rebuild a Lane 1 Channel from a persisted unit row."""

    return Channel(kind=ChannelKind(unit.kind), ordinal=unit.ordinal, text=unit.raw_text)


def candidate_block(candidate: AnalyzedClaim) -> str:
    lines = [
        f"claim_id_hint: {candidate.claim_id_hint}",
        f"text: {candidate.text}",
        f"support_quote: {candidate.support_quote}",
        f"confidence: {candidate.confidence}",
    ]
    if candidate.notes:
        lines.append(f"notes: {candidate.notes}")
    if candidate.entities:
        lines.append(
            "entities: "
            + "; ".join(f"{entity.name} ({entity.type})" for entity in candidate.entities)
        )
    if candidate.relations:
        lines.append(
            "relations: "
            + "; ".join(
                f"{relation.subject} {relation.predicate} {relation.object}"
                for relation in candidate.relations
            )
        )
    return "<untrusted_candidate>\n" + _escape("\n".join(lines)) + "\n</untrusted_candidate>"


def neighborhood_block(claims: Sequence[NeighborhoodClaim]) -> str:
    if not claims:
        body = "(empty — the graph has no existing claims touching these entities)"
    else:
        body = "\n".join(
            f"claim:{item.id} entities={','.join(item.entities)} :: "
            f"{normalize_claim_text(item.text)}"
            for item in claims
        )
    return "<untrusted_neighborhood>\n" + _escape(body) + "\n</untrusted_neighborhood>"


def claim_block(text: str) -> str:
    return "<untrusted_claim>\n" + _escape(normalize_claim_text(text)) + "\n</untrusted_claim>"
