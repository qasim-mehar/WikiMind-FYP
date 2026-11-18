"""The governance-verbatim answer-composition prompt and its response model.

This prompt is a pinned artifact, stored byte-for-byte from the Lane 7 card.
Like the three compile prompts, any change — including whitespace — requires
a new governance instruction and a digest update in ``tests/test_qa_prompts.py``.

Only the user message (the question plus the retrieved, untrusted claim block)
is appended after this prompt; the prompt itself is never reformatted.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

QA_PROMPT_VERSION = "qa-v0"

# Canonical call sites. The "qa." prefix already exists in
# wikimind.llm.cost.CALL_SITE_PREFIXES; cost.py is deliberately not edited.
CALL_SITE_ANSWER = "qa.answer"
CALL_SITE_EMBED = "qa.embed"

QA_ANSWER_PROMPT = """You are the answer composer for a knowledge-base system.
You receive one user question and a set of stored claims retrieved from
the knowledge graph. Each claim has an identifier, its exact text, and
the document it was extracted from.

Rules:
1. Answer using ONLY the provided claims. Use no outside knowledge.
2. Every factual sentence in your answer must be supported by at least
   one provided claim.
3. After each supported sentence, cite the supporting claim identifiers
   in square brackets, like [c-1234].
4. If the provided claims conflict, present both sides and cite both.
   Never resolve a contradiction silently.
5. If the claims do not contain enough information to answer, say so
   plainly and cite what is relevant. Never guess.
6. Return JSON only, no other text:
   {"answer": "<text with [claim_id] citations>",
    "sufficient": <true|false>,
    "citations": [{"claim_id": "<id>", "document_slug": "<slug>"}]}"""


class AnswerCitation(BaseModel):
    """One claim citation returned by the answer composer."""

    # extra="ignore" (not "forbid") so the generated JSON schema contains no
    # additionalProperties key: google-genai forwards it as an unsupported
    # "additional_properties" field and the live API rejects the request.
    model_config = ConfigDict(frozen=True, extra="ignore")

    claim_id: str = Field(min_length=1)
    document_slug: str = Field(min_length=1)


class AnswerPayload(BaseModel):
    """The exact JSON shape the answer composer must return."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    answer: str = Field(min_length=1)
    sufficient: bool
    citations: list[AnswerCitation] = Field(default_factory=list)


def claim_ref(claim_id: UUID) -> str:
    """The short, human-citable identifier shown to the model (e.g. c-1a2b3c4d5e6f)."""

    return f"c-{claim_id.hex[:12]}"


def _escape(text: str) -> str:
    """Escape XML-sensitive characters so distilled text cannot break a wrapper tag."""

    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class PromptClaim(BaseModel):
    """The minimal, provider-safe view of a claim passed into the prompt."""

    model_config = ConfigDict(frozen=True)

    ref: str
    text: str
    document_slug: str


def build_user_message(question: str, claims: Sequence[PromptClaim]) -> str:
    """Render the question and the untrusted claim block appended after the prompt.

    Distilled claim text is untrusted (D4/D8): it is XML-escaped and enclosed in
    an explicit ``<untrusted>`` element so injected instructions can never be
    mistaken for system text. The question is escaped for the same reason.
    """

    parts = [
        "<question>",
        _escape(question),
        "</question>",
        "",
        '<untrusted source="knowledge-graph-claims">',
    ]
    for claim in claims:
        parts.append(
            f'<claim id="{_escape(claim.ref)}" document="{_escape(claim.document_slug)}">'
            f"{_escape(claim.text)}</claim>"
        )
    parts.extend(
        [
            "</untrusted>",
            "",
            "Answer using only the claims in the untrusted block above. Return JSON only.",
        ]
    )
    return "\n".join(parts)
