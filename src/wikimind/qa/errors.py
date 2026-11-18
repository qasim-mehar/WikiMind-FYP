"""Clean, user-facing QA errors (never raw stack traces at the CLI boundary)."""

from __future__ import annotations

from wikimind.llm.errors import WikiMindLLMError


class QAError(Exception):
    """Base class for errors the QA layer can present cleanly."""


class QARetrievalError(QAError):
    """Retrieval failed (bad connection, invalid project, malformed graph)."""


class AnswerFormatError(QAError):
    """The drafter did not return a valid answer payload after the one retry."""


class AnswerGenerationError(QAError):
    """The drafter failed after exhausting its transport retries."""

    def __init__(self, message: str, cause: WikiMindLLMError | None = None) -> None:
        super().__init__(message)
        self.cause = cause
