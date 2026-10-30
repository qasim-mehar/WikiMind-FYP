"""The compile pipeline: five stages per channel unit, one pass, no loops.

Per document registered in the project, over each channel unit from Lane 1:

1. ANALYZE (drafter)      — passage -> candidate atomic claims.
2. Dedup pre-filter (SQL) — normalized-text hash; exact duplicates never
   reach an LLM.
3. WRITE (drafter)        — candidate + existing graph neighborhood ->
   final standalone claim text + disposition.
4. JUDGE (judge)          — passage + FINAL claim text -> {entailed, score}.
5. COMMIT (no LLM)        — accepted claims are written inside the canonical
   writer lock with full provenance, touched nodes get a node version
   materialized (Governance Ruling 14), then the unit marker records the
   content-hash short-circuit.

Single-pass discipline (V0): a rejected claim is counted, never retried by
another LLM round.  Contradictions are recorded, never resolved: both
claims persist and a typed edge links them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from ..config import redact
from ..llm.protocols import LLMClient
from . import graph
from .blocks import (
    candidate_block,
    channel_for_unit,
    claim_block,
    neighborhood_block,
    passage_block,
)
from .errors import CompileError
from .graph import AcceptedClaim, ProjectRef, Unit
from .models import WriteOutput
from .prompts import COMPILE_PROMPT_VERSION
from .stages import run_analyze, run_judge, run_write

COMPILE_JUDGE_MIN_SCORE = 4

DISPOSITIONS = ("new", "supersedes", "contradicts", "duplicate", "reject")


@dataclass
class CompileStats:
    """Per-run counters; serialized into ``compile_runs.stats`` (JSONB)."""

    documents_seen: int = 0
    documents_skipped: int = 0
    units_seen: int = 0
    units_skipped: int = 0
    claims_drafted: int = 0
    dedup_filtered: int = 0
    anchor_rejects: int = 0
    schema_rejects: int = 0
    judge_accepted: int = 0
    judge_rejected: int = 0
    disposition_repairs: int = 0
    claims_committed: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    node_versions: int = 0
    dispositions: dict[str, int] = field(default_factory=lambda: dict.fromkeys(DISPOSITIONS, 0))
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = {
            key: value
            for key, value in self.__dict__.items()
            if key not in {"errors"} and value is not None
        }
        data["dispositions"] = dict(self.dispositions)
        if self.errors:
            data["errors"] = list(self.errors)
        return data


@dataclass(slots=True)
class CompileRunResult:
    run_id: UUID | None
    project: ProjectRef
    status: str
    stats: CompileStats
    run_cost_usd: float


def _unit_meta(stage: str, run_id: UUID | None, unit: Unit) -> dict[str, Any]:
    meta = {
        "stage": stage,
        "document": unit.document_sha256[:16],
        "unit": unit.channel_id[:16],
    }
    if run_id is not None:
        meta["run"] = str(run_id)
    return meta


def _resolve_target(
    conn: psycopg.Connection,
    project_id: UUID,
    written: WriteOutput,
    candidate_slugs: list[str],
) -> tuple[UUID | None, str]:
    """Validate a named target for supersedes/contradicts/duplicate.

    Returns ``(target_claim_id, effective_disposition)``.  A missing target,
    an unparseable id, or a target that shares no entity with the final
    claim downgrades the disposition to ``new`` (sanity check: the link is
    only recorded when it is real).  A verified ``duplicate`` yields
    ``duplicate`` and nothing is committed.
    """

    target = graph.parse_target_claim_id(written.target_claim_id)
    if target is None:
        return None, "new"
    row = conn.execute(
        "SELECT id FROM claims WHERE id = %s AND project_id = %s",
        (target, project_id),
    ).fetchone()
    if row is None:
        return None, "new"
    target_slugs = graph.claim_entity_slugs(conn, project_id, target)
    if not target_slugs.intersection(candidate_slugs):
        return None, "new"
    return target, written.disposition


def _process_unit(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    unit: Unit,
    drafter: LLMClient,
    judge: LLMClient,
    stats: CompileStats,
    known_texts: set[str],
    run_id: UUID | None,
    compile_version: str,
    min_score: int,
    delay: float,
    neighborhood_limit: int,
    channel_budget: int,
    sleep: Any,
) -> None:
    stats.units_seen += 1

    analyzed = run_analyze(
        drafter,
        passage_block=passage_block(channel_for_unit(unit), channel_budget),
        meta=_unit_meta("analyze", run_id, unit),
        delay=delay,
        sleep=sleep,
    )
    if analyzed.schema_rejected:
        stats.schema_rejects += 1
        stats.errors.append(f"analyze schema_reject unit={unit.channel_id[:12]}")
        # The commit still runs (the citation node gets a version, Ruling 14),
        # so its counts feed the run stats like any other unit.
        counts = graph.commit_unit(
            conn,
            project_id=project_id,
            unit=unit,
            accepted=[],
            compile_version=compile_version,
            run_id=run_id,
        )
        stats.claims_committed += counts.claims
        stats.nodes_created += counts.nodes_created
        stats.edges_created += counts.edges_created
        stats.node_versions += counts.node_versions
        return
    assert analyzed.value is not None

    accepted: list[AcceptedClaim] = []
    seen_this_run: set[str] = set()
    for candidate in analyzed.value.claims:
        stats.claims_drafted += 1

        # Stage 2: dedup pre-filter — exact duplicates never reach an LLM.
        normalized = graph.normalize_claim_text(candidate.text)
        if not normalized or normalized in known_texts or normalized in seen_this_run:
            stats.dedup_filtered += 1
            continue
        seen_this_run.add(normalized)

        # Anchor sanity: the support quote must be a verbatim substring.
        if not graph.is_anchored(unit.raw_text, candidate.support_quote):
            stats.anchor_rejects += 1
            continue

        candidate_slugs = graph.entity_slugs_for(candidate.entities)
        neighborhood = graph.fetch_neighborhood(
            conn, project_id, candidate_slugs, neighborhood_limit
        )
        written_result = run_write(
            drafter,
            candidate_block=candidate_block(candidate),
            neighborhood_block=neighborhood_block(neighborhood),
            meta=_unit_meta("write", run_id, unit),
            delay=delay,
            sleep=sleep,
        )
        if written_result.schema_rejected:
            stats.schema_rejects += 1
            stats.errors.append(f"write schema_reject unit={unit.channel_id[:12]}")
            continue
        written = written_result.value
        assert written is not None
        stats.dispositions[written.disposition] = stats.dispositions.get(written.disposition, 0) + 1

        if written.disposition == "reject":
            continue

        candidate_slugs_after_write = graph.entity_slugs_for(written.entities)
        target_id: UUID | None = None
        if written.disposition in ("supersedes", "contradicts", "duplicate"):
            target_id, effective = _resolve_target(
                conn, project_id, written, candidate_slugs_after_write
            )
            if effective != written.disposition:
                stats.disposition_repairs += 1
                stats.dispositions[written.disposition] -= 1
                stats.dispositions["new"] += 1
                written = written.model_copy(update={"disposition": "new"})
            elif effective == "duplicate":
                # The neighborhood already states exactly this: record, keep
                # the existing claim, commit nothing.
                continue

        # Stage 4: JUDGE, on the post-write text, one call per claim that
        # would be committed.
        judged_result = run_judge(
            judge,
            passage_block=passage_block(channel_for_unit(unit), channel_budget),
            claim_block=claim_block(written.text),
            meta=_unit_meta("judge", run_id, unit),
            delay=delay,
            sleep=sleep,
        )
        if judged_result.schema_rejected:
            stats.schema_rejects += 1
            stats.errors.append(f"judge schema_reject unit={unit.channel_id[:12]}")
            continue
        verdict = judged_result.value
        assert verdict is not None
        if not verdict.entailed or verdict.score < min_score:
            stats.judge_rejected += 1
            continue
        stats.judge_accepted += 1

        accepted.append(
            AcceptedClaim(
                text=written.text,
                support_quote=candidate.support_quote,
                entities=tuple(written.entities),
                relations=tuple(written.relations),
                disposition=written.disposition,
                target_claim_id=target_id
                if written.disposition in ("supersedes", "contradicts")
                else None,
            )
        )

    # Stage 5: COMMIT under the canonical writer lock (claims may be empty;
    # the unit marker is always written).
    counts = graph.commit_unit(
        conn,
        project_id=project_id,
        unit=unit,
        accepted=accepted,
        compile_version=compile_version,
        run_id=run_id,
    )
    stats.claims_committed += counts.claims
    stats.nodes_created += counts.nodes_created
    stats.edges_created += counts.edges_created
    stats.node_versions += counts.node_versions
    for committed in accepted:
        known_texts.add(graph.normalize_claim_text(committed.text))


def compile_project(
    conn: psycopg.Connection,
    *,
    project: ProjectRef,
    drafter: LLMClient,
    judge: LLMClient,
    compile_version: str = COMPILE_PROMPT_VERSION,
    min_score: int = COMPILE_JUDGE_MIN_SCORE,
    delay: float = 0.0,
    neighborhood_limit: int = 20,
    channel_budget: int = 60_000,
    meter: Any | None = None,
    sleep: Any = time.sleep,
) -> CompileRunResult:
    """Compile every pending unit of every document registered in the project.

    ``conn`` must use ``dict_row``.  LLM calls go through the two clients
    (pinned models, temperature 0); ``delay`` paces them for rate limits.
    Raises on provider failure after marking the run failed.
    """

    run_id: UUID | None = None
    stats = CompileStats()
    try:
        row = conn.execute(
            "INSERT INTO compile_runs (project_id) VALUES (%s) RETURNING id",
            (project.id,),
        ).fetchone()
        run_id = row["id"]

        documents = graph.list_documents(conn, project.id)
        units_by_document: dict[UUID, list[Unit]] = {}
        for unit in graph.list_units(conn, project.id):
            units_by_document.setdefault(unit.document_id, []).append(unit)
        compiled = graph.compiled_unit_keys(conn, project.id, compile_version)
        known_texts = graph.known_claim_texts(conn, project.id)

        for document in documents:
            stats.documents_seen += 1
            document_units = units_by_document.get(document.id, [])
            pending = [unit for unit in document_units if unit.unit_key not in compiled]
            if not pending:
                stats.documents_skipped += 1
                stats.units_skipped += len(document_units)
                continue
            for unit in pending:
                _process_unit(
                    conn,
                    project_id=project.id,
                    unit=unit,
                    drafter=drafter,
                    judge=judge,
                    stats=stats,
                    known_texts=known_texts,
                    run_id=run_id,
                    compile_version=compile_version,
                    min_score=min_score,
                    delay=delay,
                    neighborhood_limit=neighborhood_limit,
                    channel_budget=channel_budget,
                    sleep=sleep,
                )

        status = "done"
    except Exception as exc:
        status = "failed"
        stats.errors.append(redact(f"run failed: {exc}"))
        _finish_run(conn, run_id, status, stats, meter)
        raise
    _finish_run(conn, run_id, status, stats, meter)
    return _result(run_id, project, status, stats, meter)


def _finish_run(
    conn: psycopg.Connection,
    run_id: UUID | None,
    status: str,
    stats: CompileStats,
    meter: Any | None,
) -> None:
    if run_id is None:
        return
    try:
        conn.execute(
            """
            UPDATE compile_runs
            SET finished_at = now(), status = %s, stats = %s
            WHERE id = %s
            """,
            (status, Json(stats.to_dict()), run_id),
        )
    except psycopg.Error:
        # The run result is already reflected in the raised/returned value;
        # losing the bookkeeping row must not mask the run outcome.
        pass


def _result(
    run_id: UUID | None,
    project: ProjectRef,
    status: str,
    stats: CompileStats,
    meter: Any | None,
) -> CompileRunResult:
    run_cost = 0.0
    if meter is not None:
        events = getattr(meter, "events", None) or []
        run_cost = round(sum(event.shadow_cost_usd for event in events), 12)
    return CompileRunResult(
        run_id=run_id,
        project=project,
        status=status,
        stats=stats,
        run_cost_usd=run_cost,
    )


__all__ = [
    "COMPILE_JUDGE_MIN_SCORE",
    "CompileError",
    "CompileRunResult",
    "CompileStats",
    "compile_project",
]
