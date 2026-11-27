"""Human-facing errors for the ingestion flow.

Every message is written to be printed verbatim after ``error:``; ``hint``
carries the next actionable step.  Exit codes follow the existing CLI
convention: 2 for configuration/input mistakes, 1 for processing failures.
"""

from __future__ import annotations


class IngestError(Exception):
    """Base class for friendly ingestion failures."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class IngestConfigError(IngestError):
    """Missing DSN, live flag, or similar setup problem."""

    exit_code = 2


class IngestInputError(IngestError):
    """Bad project reference or unusable source file."""

    exit_code = 2


class ExtractError(IngestError):
    """Parsing failed for a specific source."""


__all__ = ["ExtractError", "IngestConfigError", "IngestError", "IngestInputError"]
