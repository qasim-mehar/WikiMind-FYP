"""QA pipeline: embed -> seed (no citation nodes) -> k-hop -> claims -> one draft.

Claims are not embeddable under the frozen schema, so QA reaches them by
traversing from embedded CURRENT node versions and following claim/edge-source
provenance. Citation-type nodes are barred from *seed* selection (Q2) because
they aggregate a whole document's claims and over-match ANN; k-hop expansion
may still traverse through them.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import psycopg
from pydantic import ValidationError

from ..index.retrieve import ann_search, fulltext_search, k_hop_expansion
from ..llm.cost import CostMeter
from ..llm.errors import WikiMindLLMError
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

# Pinned defaults. These are the "choice" the card asks to be fixed by tests.
DEFAULT_SEED_K = 20  # over-fetch ANN/FTS because citation nodes are filtered out
DEFAULT_HOPS = 1
DEFAULT_CLAIM_CAP = 40
CITATION_NODE_TYPE = "citation"

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
    """One retrieved claim, with the short identifier shown to the model."""

    ref: str
    claim_id: UUID
    text: str
    document_slug: str


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


def _val(row: object, key: str, index: int) -> object:
    """Read a column under either psycopg row factory."""

    return row[key] if isinstance(row, dict) else row[index]  # type: ignore[index]


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

    The node-type filter is applied *after* the substrate searches so that
    ``k_hop_expansion`` can still traverse into citation nodes later. A
    citation node with an arbitrarily large render therefore never becomes a
    seed, but remains reachable at depth >= 1 (pinned by the QA tests).
    """

    if ann_k < 1 or fts_k < 1:
        raise ValueError("seed k values must be positive")

    ann_hits = ann_search(conn, project_id, list(query_vector), k=ann_k)
    fts_hits = fulltext_search(conn, project_id, query_text, k=fts_k) if query_text else []

    ordered: dict[UUID, None] = {}
    for hit in (*ann_hits, *fts_hits):
        ordered.setdefault(hit.node_id, None)
    ids = list(ordered)
    if not ids:
        return []

    rows = conn.execute(
        "SELECT id, node_type FROM nodes WHERE project_id = %s AND id = ANY(%s)",
        (project_id, ids),
    ).fetchall()
    types = {_val(row, "id", 0): _val(row, "node_type", 1) for row in rows}
    return [node_id for node_id in ids if types.get(node_id) != CITATION_NODE_TYPE]


def gather_claims(
    conn: psycopg.Connection,
    project_id: UUID,
    visited_node_ids: Sequence[UUID],
    *,
    seed_node_ids_: Sequence[UUID] = (),
    cap: int = DEFAULT_CLAIM_CAP,
) -> list[ClaimRecord]:
    """Claims connected to the visited nodes, seed-first, deterministic, capped.

    A claim is connected when it is stored against a visited node
    (``claims.citation_node_id``) or when one of its provenance edges
    (``edge_sources``) is incident to a visited node. Seed-node claims sort
    before k-hop claims; ties break by claim id so the result is stable across
    runs and ``PYTHONHASHSEED`` values.
    """

    if cap < 1:
        raise ValueError("cap must be positive")
    if not visited_node_ids:
        return []

    visited = list(visited_node_ids)
    seeds = list(seed_node_ids_)
    rows = conn.execute(
        """
        WITH matches AS (
            SELECT c.id, c.text, cn.slug AS document_slug,
                   (c.citation_node_id = ANY(%s)) AS is_seed
            FROM claims c
            JOIN nodes cn
              ON cn.id = c.citation_node_id AND cn.project_id = c.project_id
            WHERE c.project_id = %s
              AND (
                c.citation_node_id = ANY(%s)
                OR EXISTS (
                    SELECT 1
                    FROM edge_sources es
                    JOIN edges e
                      ON e.id = es.edge_id AND e.project_id = es.project_id
                    WHERE es.project_id = c.project_id
                      AND es.claim_id = c.id
                      AND (e.src_node = ANY(%s) OR e.dst_node = ANY(%s))
                )
              )
        )
        SELECT id, text, document_slug FROM matches
        ORDER BY is_seed DESC, id
        LIMIT %s
        """,
        (seeds, project_id, visited, visited, visited, cap),
    ).fetchall()
    return [
        ClaimRecord(
            ref=claim_ref(_val(row, "id", 0)),
            claim_id=_val(row, "id", 0),
            text=_val(row, "text", 1),
            document_slug=_val(row, "document_slug", 2),
        )
        for row in rows
    ]


def _compose_answer(
    drafter: LLMClient,
    user_message: str,
    *,
    max_retries: int = QA_MAX_ATTEMPTS,
) -> AnswerPayload:
    """One drafter call, plus at most one schema-validation retry (Q3)."""

    current = user_message
    last_error: ValidationError | None = None
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

    visited: dict[UUID, None] = {}
    for node_id in (*seeds, *(hit.node_id for hit in graph)):
        visited.setdefault(node_id, None)
    try:
        claims = gather_claims(conn, project_id, list(visited), seed_node_ids_=seeds, cap=claim_cap)
    except psycopg.Error as exc:
        raise QARetrievalError(f"claim gathering failed: {exc}") from exc

    prompt_claims = [
        PromptClaim(ref=claim.ref, text=claim.text, document_slug=claim.document_slug)
        for claim in claims
    ]
    payload = _compose_answer(
        drafter, build_user_message(question, prompt_claims), max_retries=max_retries
    )

    return AnswerResult(
        answer=payload.answer,
        sufficient=payload.sufficient,
        citations=tuple(payload.citations),
        claims=tuple(claims),
        shadow_cost_usd=meter.total_shadow_cost_usd,
        ledger_events=len(meter.events),
        wall_time_s=time.perf_counter() - started,
        seeds=tuple(seeds),
        claim_cap=claim_cap,
        truncated=len(claims) >= claim_cap,
    )
