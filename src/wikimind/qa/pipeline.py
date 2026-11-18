"""QA pipeline: embed -> seed (substrate-scoped) -> k-hop -> claims -> one draft.

Claims are not embeddable under the frozen schema, so QA reaches them by
traversing from embedded CURRENT node versions and following claim/edge-source
provenance. Citation-type nodes are barred from *seed* selection at the SQL
level (Q2/F2); k-hop expansion may still traverse through them.

Worst-case composition is 3 transport attempts x 2 validation attempts per
answer (M6/L6); the CLI help states this explicitly.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

import psycopg
from pydantic import ValidationError

from ..compile.ids import slugify_entity
from ..index.retrieve import ann_search, fulltext_search, k_hop_expansion
from ..llm.cost import CostMeter
from ..llm.errors import ResponseFormatError, WikiMindLLMError
from ..llm.protocols import Embedder, LLMClient
from .errors import AnswerFormatError, AnswerGenerationError, QARetrievalError
from .prompts import (
    CALL_SITE_ANSWER,
    QA_ANSWER_PROMPT,
    QA_PROMPT_VERSION,
    AnswerCitation,
    AnswerPayload,
    PromptClaim,
    build_user_message,
    claim_ref,
)
from .verify import VerificationResult, verify_citations

# Pinned defaults. These are the "choice" the card asks to be fixed by tests.
DEFAULT_SEED_K = 20
DEFAULT_HOPS = 1
DEFAULT_CLAIM_CAP = 40
CITATION_NODE_TYPE = "citation"

# Frozen schema status domain: active|stale|superseded|archived|retracted.
# Tombstoned/archived and retracted provenance never contributes to an answer;
# stale/superseded nodes survive but their status surfaces on the citation.
EXCLUDED_NODE_STATUSES = ("archived", "retracted")

# Q4: at most three transport attempts with exponential backoff. The curve is
# base_delay * 2**(attempt-1) (0.5s, 1.0s), capped by the shared retry helper.
QA_MAX_ATTEMPTS = 3
QA_BASE_DELAY = 0.5
QA_TIMEOUT_S = 120.0

# Q3: a malformed payload gets exactly one schema-validation retry.
VALIDATION_RETRIES = 1

_VALIDATION_NUDGE = (
    "\n\nYour previous response did not match the required JSON shape. "
    "Return only the required JSON object."
)


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    """One retrieved claim, with the short id and canonical document slug."""

    ref: str
    claim_id: UUID
    text: str
    document_slug: str
    node_status: str = "active"


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """Everything the CLI prints after a successful answer."""

    answer: str
    sufficient: bool
    citations: tuple[AnswerCitation, ...]
    claims: tuple[ClaimRecord, ...]
    shadow_cost_usd: float
    ledger_events: int
    wall_time_s: float
    seeds: tuple[UUID, ...]
    claim_cap: int
    truncated: bool
    unverified_removed: int = 0


def _val(row: object, key: str, index: int) -> object:
    """Read a column under either psycopg row factory."""

    return row[key] if isinstance(row, dict) else row[index]  # type: ignore[index]


def document_slug(source_uri: str) -> str:
    """Ruling 21 canonical document slug: slugified filename stem."""

    name = PurePosixPath(source_uri.replace("\\", "/")).name
    return slugify_entity(PurePosixPath(name).stem) or "document"


def seed_node_ids(
    conn: psycopg.Connection,
    project_id: UUID,
    query_vector: Sequence[float],
    query_text: str,
    *,
    ann_k: int = DEFAULT_SEED_K,
    fts_k: int = DEFAULT_SEED_K,
) -> list[UUID]:
    """ANN + full-text seeds, deterministically ordered, citation nodes removed.

    The type filter lives in the substrate SQL *before* the LIMIT (F2), so a
    corpus whose top-k is entirely citation nodes still yields real seeds.
    ``k_hop_expansion`` is a separate call and remains free to traverse them.
    """

    if ann_k < 1 or fts_k < 1:
        raise ValueError("seed k values must be positive")

    ann_hits = ann_search(
        conn,
        project_id,
        list(query_vector),
        k=ann_k,
        exclude_node_types=(CITATION_NODE_TYPE,),
    )
    fts_hits = (
        fulltext_search(
            conn,
            project_id,
            query_text,
            k=fts_k,
            exclude_node_types=(CITATION_NODE_TYPE,),
        )
        if query_text
        else []
    )

    ordered: dict[UUID, None] = {}
    for hit in (*ann_hits, *fts_hits):
        ordered.setdefault(hit.node_id, None)
    return list(ordered)


def _gather_claims_page(
    conn: psycopg.Connection,
    project_id: UUID,
    depths: Mapping[UUID, int],
    *,
    cap: int,
) -> tuple[list[ClaimRecord], bool]:
    """Fetch at most ``cap`` claims ranked by (minimum connecting depth, id).

    ``depths`` maps visited node ids to their hop distance from a seed; seeds
    are 0. A claim's rank is the shallowest visited node that connects to it
    (F2/M1), so genuinely seed-adjacent claims sort before deeper ones. Rows
    whose citation node or connecting provenance node is archived/retracted
    are excluded (F3). Fetching ``cap + 1`` rows makes ``truncated`` exact.
    """

    if cap < 1:
        raise ValueError("cap must be positive")
    if not depths:
        return [], False

    node_ids = list(depths)
    depth_values = [depths[node_id] for node_id in node_ids]
    rows = conn.execute(
        """
        WITH depth_map(node_id, depth) AS (
            SELECT dm.node_id, dm.depth
            FROM unnest(%s::uuid[], %s::int[]) AS dm(node_id, depth)
            JOIN nodes vn ON vn.id = dm.node_id AND vn.project_id = %s
            WHERE vn.status <> ALL(%s::text[])
        ),
        connections AS (
            SELECT c.id AS claim_id, MIN(dm.depth) AS best_depth
            FROM claims c
            JOIN depth_map dm
              ON dm.node_id = c.citation_node_id
              OR EXISTS (
                  SELECT 1
                  FROM edge_sources es
                  JOIN edges e
                    ON e.id = es.edge_id AND e.project_id = es.project_id
                  WHERE es.project_id = c.project_id
                    AND es.claim_id = c.id
                    AND (e.src_node = dm.node_id OR e.dst_node = dm.node_id)
              )
            WHERE c.project_id = %s
            GROUP BY c.id
        )
        SELECT c.id, c.text, d.source_uri, cn.status
        FROM connections conn
        JOIN claims c ON c.id = conn.claim_id AND c.project_id = %s
        JOIN nodes cn ON cn.id = c.citation_node_id AND cn.project_id = c.project_id
        JOIN documents d ON d.id = c.document_id AND d.project_id = c.project_id
        WHERE cn.status <> ALL(%s::text[])
        ORDER BY conn.best_depth, c.id
        LIMIT %s
        """,
        (
            node_ids,
            depth_values,
            project_id,
            list(EXCLUDED_NODE_STATUSES),
            project_id,
            project_id,
            list(EXCLUDED_NODE_STATUSES),
            cap + 1,
        ),
    ).fetchall()

    truncated = len(rows) > cap
    return [
        ClaimRecord(
            ref=claim_ref(_val(row, "id", 0)),
            claim_id=_val(row, "id", 0),
            text=_val(row, "text", 1),
            document_slug=document_slug(_val(row, "source_uri", 2)),
            node_status=str(_val(row, "status", 3)),
        )
        for row in rows[:cap]
    ], truncated


def gather_claims(
    conn: psycopg.Connection,
    project_id: UUID,
    visited_node_ids: Sequence[UUID],
    *,
    seed_node_ids_: Sequence[UUID] = (),
    cap: int = DEFAULT_CLAIM_CAP,
    depths: Mapping[UUID, int] | None = None,
) -> list[ClaimRecord]:
    """Claims connected to the visited nodes, seed-first, deterministic, capped."""

    if depths is None:
        seeds = set(seed_node_ids_)
        depths = {node_id: (0 if node_id in seeds else 1) for node_id in list(visited_node_ids)}
    claims, _ = _gather_claims_page(conn, project_id, depths, cap=cap)
    return claims


def _compose_answer(
    drafter: LLMClient,
    user_message: str,
    *,
    max_retries: int = QA_MAX_ATTEMPTS,
) -> AnswerPayload:
    """One drafter call, plus at most one validation retry (Q3/F4).

    A validation failure is either a Pydantic error on a raw fake response or
    the production client's ``ResponseFormatError``; both trigger the nudge.
    """

    current = user_message
    last_error: ValidationError | ResponseFormatError | None = None
    for _ in range(VALIDATION_RETRIES + 1):
        try:
            result = drafter.complete(
                system=QA_ANSWER_PROMPT,
                user=current,
                response_schema=AnswerPayload,
                call_site=CALL_SITE_ANSWER,
                max_retries=max_retries,
                meta={"arm": "qa", "prompt_version": QA_PROMPT_VERSION},
            )
        except ResponseFormatError as exc:
            last_error = exc
            current = user_message + _VALIDATION_NUDGE
            continue
        except WikiMindLLMError as exc:
            raise AnswerGenerationError(f"answer generation failed: {exc}", cause=exc) from exc
        try:
            return AnswerPayload.model_validate(result.json)
        except ValidationError as exc:
            last_error = exc
            current = user_message + _VALIDATION_NUDGE
    if last_error is None:
        raise AnswerFormatError("the drafter returned no answer")
    raise AnswerFormatError(
        f"the drafter did not return a valid answer after one retry: {last_error}"
    )


def answer_question(
    conn: psycopg.Connection,
    project_id: UUID,
    question: str,
    *,
    embedder: Embedder,
    drafter: LLMClient,
    meter: CostMeter,
    ann_k: int = DEFAULT_SEED_K,
    fts_k: int = DEFAULT_SEED_K,
    hops: int = DEFAULT_HOPS,
    claim_cap: int = DEFAULT_CLAIM_CAP,
    max_retries: int = QA_MAX_ATTEMPTS,
) -> AnswerResult:
    """Answer one question from the compiled graph using a single drafter call."""

    if not question.strip():
        raise ValueError("question must not be empty")
    started = time.perf_counter()

    try:
        vector = embedder.embed([question])[0]
        seeds = seed_node_ids(conn, project_id, vector, question, ann_k=ann_k, fts_k=fts_k)
        graph = k_hop_expansion(conn, project_id, seeds, hops=hops)
    except psycopg.Error as exc:
        raise QARetrievalError(f"retrieval failed: {exc}") from exc
    except (IndexError, ValueError) as exc:
        raise QARetrievalError(f"question embedding failed: {exc}") from exc

    depths: dict[UUID, int] = {node_id: 0 for node_id in seeds}
    for hit in graph:
        depths[hit.node_id] = min(depths.get(hit.node_id, hit.depth), hit.depth)
    try:
        claims, truncated = _gather_claims_page(conn, project_id, depths, cap=claim_cap)
    except psycopg.Error as exc:
        raise QARetrievalError(f"claim gathering failed: {exc}") from exc

    prompt_claims = [
        PromptClaim(ref=claim.ref, text=claim.text, document_slug=claim.document_slug)
        for claim in claims
    ]
    payload = _compose_answer(
        drafter, build_user_message(question, prompt_claims), max_retries=max_retries
    )
    verified: VerificationResult = verify_citations(payload, prompt_claims)

    return AnswerResult(
        answer=payload.answer,
        sufficient=payload.sufficient,
        citations=verified.citations,
        claims=tuple(claims),
        shadow_cost_usd=meter.total_shadow_cost_usd,
        ledger_events=len(meter.events),
        wall_time_s=time.perf_counter() - started,
        seeds=tuple(seeds),
        claim_cap=claim_cap,
        truncated=truncated,
        unverified_removed=verified.unverified_removed,
    )
