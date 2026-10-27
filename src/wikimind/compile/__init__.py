"""Compile lane: source channels -> verified, provenance-carrying claims."""

from __future__ import annotations

from .errors import AmbiguousProjectError, CompileError, ProjectNotFoundError
from .graph import (
    AcceptedClaim,
    CommitCounts,
    DocumentRow,
    NeighborhoodClaim,
    ProjectRef,
    Unit,
    commit_unit,
    create_project,
    list_documents,
    list_units,
    register_document,
    resolve_project,
)
from .pipeline import (
    COMPILE_JUDGE_MIN_SCORE,
    CompileRunResult,
    CompileStats,
    compile_project,
)
from .prompts import (
    ANALYZE_PROMPT,
    CALL_SITE_ANALYZE,
    CALL_SITE_JUDGE,
    CALL_SITE_WRITE,
    COMPILE_PROMPT_VERSION,
    JUDGE_PROMPT,
    WRITE_PROMPT,
)
from .stages import run_analyze, run_judge, run_write

__all__ = [
    "ANALYZE_PROMPT",
    "CALL_SITE_ANALYZE",
    "CALL_SITE_JUDGE",
    "CALL_SITE_WRITE",
    "COMPILE_JUDGE_MIN_SCORE",
    "COMPILE_PROMPT_VERSION",
    "JUDGE_PROMPT",
    "WRITE_PROMPT",
    "AcceptedClaim",
    "AmbiguousProjectError",
    "CommitCounts",
    "CompileError",
    "CompileRunResult",
    "CompileStats",
    "DocumentRow",
    "NeighborhoodClaim",
    "ProjectNotFoundError",
    "ProjectRef",
    "Unit",
    "commit_unit",
    "compile_project",
    "create_project",
    "list_documents",
    "list_units",
    "register_document",
    "resolve_project",
    "run_analyze",
    "run_judge",
    "run_write",
]
