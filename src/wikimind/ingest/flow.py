"""Orchestration for the friendly ``wikimind add`` flow.

One command extracts, registers, estimates, confirms and compiles source
files.  The heavy lifting still lives in the compile lane (single-writer
project lock, deterministic ids, provenance-bearing commits); this module only
sequences those steps, makes idempotency decisions, and produces readable
progress.  It never bypasses the writer lock for a mutation.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from ..compile.errors import AmbiguousProjectError, ProjectNotFoundError
from ..compile.graph import (
    Unit,
    compiled_unit_keys,
    list_units,
    register_document,
    resolve_project,
)
from ..compile.pipeline import COMPILE_JUDGE_MIN_SCORE, CompileRunResult, compile_project
from ..compile.prompts import COMPILE_PROMPT_VERSION
from ..config import redact
from ..db.locks import with_writer_lock
from ..integrity.costs import build_meter
from ..llm.errors import WikiMindLLMError
from ..llm.gemini import GeminiClient
from ..llm.judge import JudgeClient
from ..llm.protocols import LLMClient
from ..parse import ParseError, parse_file
from .errors import ExtractError, IngestConfigError, IngestError, IngestInputError
from .estimate import DEFAULT_CLAIMS_PER_UNIT, CostEstimate, estimate_compile_cost

Progress = Callable[[str], None]
Confirm = Callable[[CostEstimate], bool]

LIVE_ENV_VAR = "WIKIMIND_LIVE"


@dataclass(slots=True)
class ExtractedDocument:
    """One source file after extraction and idempotent registration."""

    path: str
    document_id: UUID
    parser: str
    channels: int
    created: bool
    units: int = 0
    pending_units: int = 0


@dataclass(slots=True)
class IngestOutcome:
    """Everything the CLI needs to report after an ``add`` run."""

    project_name: str
    extracted: list[ExtractedDocument] = field(default_factory=list)
    estimate: CostEstimate | None = None
    compiled: bool = False
    skipped_reason: str | None = None
    run: CompileRunResult | None = None
    shadow_cost_usd: float = 0.0

    @property
    def new_documents(self) -> int:
        return sum(1 for item in self.extracted if item.created)

    @property
    def already_registered(self) -> int:
        return sum(1 for item in self.extracted if not item.created)

    @property
    def pending_units(self) -> int:
        return sum(item.pending_units for item in self.extracted)


def _noop_progress(_message: str) -> None:
    return None


class _ProgressLLM:
    """Wrap an LLM client to surface per-call progress without changing it."""

    def __init__(
        self,
        client: LLMClient,
        *,
        counter: dict[str, int],
        progress: Progress,
    ) -> None:
        self._client = client
        self._counter = counter
        self._progress = progress

    def complete(self, **kwargs: Any) -> Any:
        result = self._client.complete(**kwargs)
        self._counter["n"] += 1
        self._progress(f"  [compile] call {self._counter['n']}: {kwargs.get('call_site', 'llm')}")
        return result


def _resolve_or_create_project(
    conn: psycopg.Connection,
    ref: str,
    *,
    create: bool,
) -> Any:
    from ..compile.graph import create_project

    try:
        return resolve_project(conn, ref)
    except ProjectNotFoundError:
        if not create:
            raise IngestInputError(
                f"no project named {ref!r}",
                hint=(
                    "create it first with `wikimind project create <name>` or pass --create-project"
                ),
            ) from None
        return create_project(conn, ref)
    except AmbiguousProjectError as exc:
        raise IngestInputError(str(exc), hint="pass the project UUID instead of its name") from exc


def _extract_one(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    file: str | Path,
    parser: str | None,
) -> ExtractedDocument:
    from ..parse import UnknownParserError, parser_names

    path = Path(file)
    if not path.exists():
        raise IngestInputError(
            f"file not found: {path}",
            hint="check the path; add accepts one or more existing source files",
        )
    if not path.is_file():
        raise IngestInputError(f"not a regular file: {path}", hint="add expects files, not folders")
    try:
        parsed = parse_file(path, parser=parser)
    except UnknownParserError as exc:
        raise IngestInputError(
            str(exc),
            hint=f"registered parsers: {', '.join(parser_names())}; pass one with --parser",
        ) from exc
    except (ParseError, OSError, UnicodeDecodeError, ValueError) as exc:
        raise ExtractError(
            f"could not extract {path.name}: {redact(exc)}",
            hint="the source may be corrupt or the chosen parser may be wrong",
        ) from exc
    if not parsed.channels or not any(channel.text.strip() for channel in parsed.channels):
        raise ExtractError(
            f"no text extracted from {path.name}",
            hint="the file may be empty or image-only; OCR is not available in Stage 1",
        )
    try:
        with with_writer_lock(project_id, conn):
            document_id, created = register_document(
                conn, project_id, parsed, source_uri=str(path.resolve())
            )
    except Exception as exc:
        raise ExtractError(
            f"could not register {path.name}: {redact(exc)}",
            hint="check the database connection and that migrations are applied",
        ) from exc
    return ExtractedDocument(
        path=str(path),
        document_id=document_id,
        parser=parsed.parser,
        channels=len(parsed.channels),
        created=created,
    )


def _pending_for(
    conn: psycopg.Connection,
    project_id: UUID,
    compile_version: str,
) -> tuple[list[Unit], dict[UUID, list[Unit]]]:
    """All pending units of the project, plus a per-document index.

    ``compile_project`` compiles every pending unit in a project, so the
    estimate must cover all of them — not just the files passed to ``add``.
    Units already marked for this compile version are skipped by the engine,
    which is what makes re-adding a document a no-op.
    """

    units = list_units(conn, project_id)
    compiled = compiled_unit_keys(conn, project_id, compile_version)
    pending = [unit for unit in units if unit.unit_key not in compiled]
    by_document: dict[UUID, list[Unit]] = defaultdict(list)
    for unit in pending:
        by_document[unit.document_id].append(unit)
    return pending, by_document


def add_documents(
    conn: psycopg.Connection,
    *,
    project_ref: str,
    files: Sequence[str | Path],
    parser: str | None = None,
    dsn: str | None = None,
    costs_path: str | Path | None = None,
    delay: float | None = None,
    min_score: int = COMPILE_JUDGE_MIN_SCORE,
    force: bool = False,
    create_project: bool = False,
    do_compile: bool = True,
    claims_per_unit: int = DEFAULT_CLAIMS_PER_UNIT,
    confirm: Confirm | None = None,
    drafter: LLMClient | None = None,
    judge: LLMClient | None = None,
    meter: Any | None = None,
    progress: Progress | None = None,
) -> IngestOutcome:
    """Add sources to a project and (by default) compile pending units.

    Idempotency: re-adding already-compiled files is a no-op — their units are
    skipped at the engine's unit-marker level, so no duplicate claims or work
    occur.  ``force`` clears the unit markers for the supplied documents so
    distillation re-runs (regeneration, not replay); deterministic claim IDs
    and guarded appends keep that safe.

    ``confirm`` is called with the estimate just before compiling.  A ``None``
    callback means "auto-confirm" (library/test callers); the CLI always
    supplies one.  Injected ``drafter``/``judge``/``meter`` bypass the
    ``WIKIMIND_LIVE`` guard so tests can run entirely on fakes.
    """

    emit = progress or _noop_progress
    project = _resolve_or_create_project(conn, project_ref, create=create_project)
    outcome = IngestOutcome(project_name=project.name)

    emit(f"[1/3] extract: {len(files)} file(s)")
    for file in files:
        item = _extract_one(conn, project_id=project.id, file=file, parser=parser)
        emit(
            f"  {'registered' if item.created else 'already registered'}: "
            f"{Path(item.path).name} "
            f"sha={_short_sha(conn, project.id, item.document_id)} "
            f"parser={item.parser} channels={item.channels}"
        )
        outcome.extracted.append(item)

    if not do_compile:
        outcome.skipped_reason = "compile disabled (--no-compile)"
        return outcome

    compile_version = COMPILE_PROMPT_VERSION
    if force:
        document_ids = [item.document_id for item in outcome.extracted]
        with with_writer_lock(project.id, conn):
            cursor = conn.execute(
                """
                DELETE FROM compile_units
                WHERE project_id = %s AND document_id = ANY(%s) AND compile_version = %s
                """,
                (project.id, document_ids, compile_version),
            )
            cleared = cursor.rowcount
        emit(f"  --force: cleared {cleared} unit marker(s); distillation will re-run")

    pending, by_document = _pending_for(conn, project.id, compile_version)
    for item in outcome.extracted:
        item.units = sum(1 for unit in list_units(conn, project.id, document_id=item.document_id))
        item.pending_units = len(by_document.get(item.document_id, []))

    if not pending:
        outcome.skipped_reason = "already compiled (nothing to do)"
        emit("[2/3] estimate: skipped — no pending units")
        return outcome

    emit("[2/3] estimate")
    estimate = estimate_compile_cost(
        [unit.raw_text for unit in pending],
        claims_per_unit=claims_per_unit,
    )
    outcome.estimate = estimate
    emit(estimate.describe())

    if confirm is not None and not confirm(estimate):
        outcome.skipped_reason = "declined by user"
        emit("[3/3] compile: skipped (no LLM calls made)")
        return outcome

    active_meter = meter if meter is not None else build_meter(costs_path, dsn=dsn)
    if drafter is None or judge is None:
        if os.environ.get(LIVE_ENV_VAR) != "1":
            raise IngestConfigError(
                "live compile requires WIKIMIND_LIVE=1 (it issues real, metered LLM calls)",
                hint="set WIKIMIND_LIVE=1 for a real add, or pass --no-compile to only register",
            )
        drafter = GeminiClient(meter=active_meter, temperature=0.0)
        judge = JudgeClient(meter=active_meter, temperature=0.0)

    counter = {"n": 0}
    progress_llm = _ProgressLLM(drafter, counter=counter, progress=emit)
    progress_judge = _ProgressLLM(judge, counter=counter, progress=emit)
    emit(f"[3/3] compile: {len(pending)} pending unit(s)")

    try:
        result = compile_project(
            conn,
            project=project,
            drafter=progress_llm,
            judge=progress_judge,
            min_score=min_score,
            delay=delay if delay is not None else 0.0,
            meter=active_meter,
        )
    except WikiMindLLMError:
        raise
    except Exception as exc:
        raise IngestError(
            f"compile failed: {redact(exc)}",
            hint="no partial run is hidden; inspect the run row and retry `wikimind add`",
        ) from exc

    outcome.run = result
    outcome.compiled = True
    outcome.shadow_cost_usd = float(getattr(active_meter, "total_shadow_cost_usd", 0.0))
    return outcome


def _short_sha(conn: psycopg.Connection, project_id: UUID, document_id: UUID) -> str:
    row = conn.execute(
        "SELECT sha256 FROM documents WHERE id = %s AND project_id = %s",
        (document_id, project_id),
    ).fetchone()
    return "?" if row is None else str(row["sha256"])[:12]


__all__ = ["ExtractedDocument", "IngestOutcome", "add_documents"]
