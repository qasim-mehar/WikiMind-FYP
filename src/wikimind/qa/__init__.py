"""Stage 1 QA: answer questions by traversing the compiled knowledge graph."""

from __future__ import annotations

from .errors import (
    AnswerFormatError,
    AnswerGenerationError,
    QAError,
    QARetrievalError,
)
from .pipeline import (
    AnswerResult,
    ClaimRecord,
    answer_question,
    gather_claims,
    seed_node_ids,
)
from .prompts import (
    CALL_SITE_ANSWER,
    CALL_SITE_EMBED,
    QA_ANSWER_PROMPT,
    AnswerCitation,
    AnswerPayload,
    PromptClaim,
    build_user_message,
    claim_ref,
)

__all__ = [
    "CALL_SITE_ANSWER",
    "CALL_SITE_EMBED",
    "QA_ANSWER_PROMPT",
    "AnswerCitation",
    "AnswerFormatError",
    "AnswerGenerationError",
    "AnswerPayload",
    "AnswerResult",
    "ClaimRecord",
    "PromptClaim",
    "QAError",
    "QARetrievalError",
    "answer_question",
    "build_user_message",
    "claim_ref",
    "gather_claims",
    "seed_node_ids",
]
