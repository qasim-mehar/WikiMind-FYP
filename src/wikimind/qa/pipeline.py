"""QA pipeline: embed -> seed (substrate-scoped) -> k-hop -> claims -> one draft.

Claims are not embeddable under the frozen schema, so QA reaches them by
traversing from embedded CURRENT node versions and following claim/edge-source
provenance. Citation-type nodes are barred from *seed* selection at the SQL
level (Q2/F2); k-hop expansion may still traverse through them.

Lane 11 (Ruling 26 remediation): a visited citation hub may nonetheless join
the claim-gathering connecting-node set directly. Its attached claims are
ranked at query time by the local embedder (nothing persisted, no schema
change) and the hub is exempt from the per-node quota, so a hub-attached claim
competes with a nonzero, relevance-derived score instead of score 0.0.

Worst-case composition is 3 transport attempts x 2 validation attempts per
answer (M6/L6); the CLI help states this explicitly.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

import psycopg
from pydantic import ValidationError

from ..compile.ids import slugify_entity
from ..index.retrieve import ann_search, fulltext_search, k_hop_expansion
from ..index.store import format_vector
from ..llm.cost import CostMeter
from ..llm.errors import ResponseFormatError, WikiMindLLMError
from ..llm.protocols import Embedder, LLMClient
from ..prices import EMBED_MODEL
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
# W1 (Ruling 25): raise recall after the acceptance diseases were diagnosed.
# A hub entity can own dozens of claims; without a quota it floods the cap and
# starves the rest of the retrieved neighborhood.
DEFAULT_CLAIM_CAP = 150
DEFAULT_NODE_QUOTA = 8
# Lane 11: the citation-hub seeding exception is on by default. Passing False
# to answer_question restores the W1-only behaviour for A/B tests.
DEFAULT_HUB_CLAIM_RANKING = True
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


def _unit_interval(value: float) -> float:
    """Map a retrieval score into [0, 1] without changing its ordering."""

    if math.isnan(value):
        return 0.0
    return max(0.0, min(1.0, value))


def seed_node_ids_with_scores(
    conn: psycopg.Connection,
    project_id: UUID,
    query_vector: Sequence[float],
    query_text: str,
    *,
    ann_k: int = DEFAULT_SEED_K,
    fts_k: int = DEFAULT_SEED_K,
) -> list[tuple[UUID, float]]:
    """ANN + FTS seeds, first-seen order, paired with a match score in [0, 1].

    Cosine similarity is mapped from [-1, 1] to [0, 1]; FTS ``ts_rank`` values
    are divided by their own maximum so they are comparable on the same axis.
    A node found by both signals keeps its strongest score. The score is the
    ranking signal R6 propagates to claim ordering; seed *order* is unchanged
    so existing first-seen-order assertions still hold.
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

    fts_max = max((hit.rank for hit in fts_hits), default=0.0)
    scores: dict[UUID, float] = {}
    for hit in ann_hits:
        scores.setdefault(hit.node_id, _unit_interval((hit.similarity + 1.0) / 2.0))
    for hit in fts_hits:
        value = _unit_interval(hit.rank / fts_max) if fts_max > 0 else 0.0
        scores[hit.node_id] = max(scores.get(hit.node_id, 0.0), value)
    return [(node_id, score) for node_id, score in scores.items()]


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

    return [
        node_id
        for node_id, _ in seed_node_ids_with_scores(
            conn, project_id, query_vector, query_text, ann_k=ann_k, fts_k=fts_k
        )
    ]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors, in [-1, 1]."""

    if len(a) != len(b):
        raise ValueError("vectors must have the same dimension")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def citation_hub_scores(
    conn: psycopg.Connection,
    project_id: UUID,
    query_vector: Sequence[float],
) -> dict[UUID, float]:
    """Match scores for CURRENT citation-node versions against the query.

    Citation nodes are barred from seed selection (F2), but a visited hub may
    still enter claim gathering with a real retrieval score instead of 0.0.
    The stored BGE embedding of the hub's CURRENT version is read only; the
    query vector is supplied by the caller's metered embedder.
    """

    rows = conn.execute(
        """
        SELECT n.id, 1 - (e.vec <=> %s::vector) AS similarity
        FROM nodes n
        JOIN embeddings e
          ON e.node_version_id = n.current_version_id AND e.project_id = n.project_id
        WHERE n.project_id = %s AND n.node_type = %s AND e.model = %s
        ORDER BY n.id
        """,
        (format_vector(list(query_vector)), project_id, CITATION_NODE_TYPE, EMBED_MODEL),
    ).fetchall()
    return {
        _val(row, "id", 0): _unit_interval((float(_val(row, "similarity", 1)) + 1.0) / 2.0)
        for row in rows
    }


def claim_text_scores(
    embedder: Embedder,
    query_vector: Sequence[float],
    claim_texts: Mapping[UUID, str],
) -> dict[UUID, float]:
    """Query-time relevance of each hub-attached claim, from its distilled text.

    ``embedder`` is the same metered, in-process local embedder used for the
    question (call site ``embed.query``, $0 shadow). Nothing is persisted; the
    mapping is ephemeral. Claim ids are sorted first so the embedder sees a
    stable input order and the result is deterministic.
    """

    ordered_ids = sorted(claim_texts)
    if not ordered_ids:
        return {}
    vectors = embedder.embed([claim_texts[claim_id] for claim_id in ordered_ids])
    return {
        claim_id: _unit_interval((_cosine(query_vector, vector) + 1.0) / 2.0)
        for claim_id, vector in zip(ordered_ids, vectors)
    }


def hub_attached_claim_texts(conn: psycopg.Connection, project_id: UUID) -> dict[UUID, str]:
    """All claims whose citation node is a citation hub, keyed by claim id."""

    rows = conn.execute(
        """
        SELECT c.id, c.text
        FROM claims c
        JOIN nodes n ON n.id = c.citation_node_id AND n.project_id = c.project_id
        WHERE c.project_id = %s AND n.node_type = %s
        ORDER BY c.id
        """,
        (project_id, CITATION_NODE_TYPE),
    ).fetchall()
    return {_val(row, "id", 0): str(_val(row, "text", 1)) for row in rows}


def _gather_claims_page(
    conn: psycopg.Connection,
    project_id: UUID,
    depths: Mapping[UUID, int],
    *,
    cap: int,
    scores: Mapping[UUID, float] | None = None,
    per_node_quota: int | None = None,
    claim_scores: Mapping[UUID, float] | None = None,
    hub_quota_exempt: bool = False,
) -> tuple[list[ClaimRecord], bool]:
    """Fetch at most ``cap`` claims ranked by connecting-node match, depth, id.

    ``depths`` maps visited node ids to their hop distance from a seed; seeds
    are 0. ``scores`` (R6) maps those node ids to their retrieval match score:
    a claim's rank is the *strongest* score among the visited nodes connecting
    to it, so a claim on a high-similarity seed survives the cap while an
    equally deep claim on a low-similarity node is cut first. Depth breaks
    score ties and the claim id breaks final ties (determinism).

    W1: each visited node contributes at most ``per_node_quota`` claims
    (default ``None`` — effective no quota; only ``answer_question`` passes an
    explicit value, :data:`DEFAULT_NODE_QUOTA`), ranked by score/depth/id. A claim
    connecting to several nodes keeps every qualifying route, so a hub node
    can no longer crowd out the rest of the retrieved neighborhood without
    silently erasing claims that also hang off a less crowded node. Final
    claim order is the strongest connecting score, then depth, then id.
    A claim survives only through an all-active path (F3/F8). Fetching
    ``cap + 1`` rows makes ``truncated`` exact.

    Lane 11: when ``claim_scores`` is supplied, a claim connected through a
    citation hub uses that query-time text score in place of the hub's (zero)
    retrieval score, so hub-attached claims rank by relevance rather than by
    claim id. With ``hub_quota_exempt`` the hub is exempt from the per-node
    quota (the seeding exception); it still answers to the global ``cap``.
    Both default to off, preserving the W1 behaviour and all prior callers.
    """

    if cap < 1:
        raise ValueError("cap must be positive")
    if per_node_quota is not None and per_node_quota < 1:
        raise ValueError("per_node_quota must be positive")
    if not depths:
        return [], False

    node_ids = list(depths)
    depth_values = [depths[node_id] for node_id in node_ids]
    score_values = [
        float(_unit_interval(scores.get(node_id, 0.0))) if scores else 0.0 for node_id in node_ids
    ]
    claim_score_items = sorted((claim_scores or {}).items())
    claim_ids = [claim_id for claim_id, _ in claim_score_items]
    claim_score_values = [float(_unit_interval(value)) for _, value in claim_score_items]
    # A huge quota makes the window filter a no-op for callers that opt out.
    quota = per_node_quota if per_node_quota is not None else 1_000_000
    rows = conn.execute(
        """
        WITH depth_map(node_id, depth, score) AS (
            SELECT dm.node_id, dm.depth, dm.score
            FROM unnest(%s::uuid[], %s::int[], %s::float8[]) AS dm(node_id, depth, score)
            JOIN nodes vn ON vn.id = dm.node_id AND vn.project_id = %s
            WHERE vn.status <> ALL(%s::text[])
        ),
        claim_score_map(claim_id, score) AS (
            SELECT cs.claim_id, cs.score
            FROM unnest(%s::uuid[], %s::float8[]) AS cs(claim_id, score)
        ),
        connections AS (
            SELECT DISTINCT c.id AS claim_id, dm.node_id AS connecting_node,
                   dm.depth AS depth,
                   CASE WHEN n.node_type = 'citation'
                        THEN GREATEST(dm.score, COALESCE(csm.score, 0.0))
                        ELSE dm.score END AS score
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
                    -- F8: every node on the provenance path must be active;
                    -- the visited endpoint is already active via depth_map.
                    AND NOT EXISTS (
                        SELECT 1 FROM nodes en
                        WHERE en.project_id = c.project_id
                          AND en.id IN (e.src_node, e.dst_node)
                          AND en.status = ANY(%s::text[])
                    )
              )
            JOIN nodes n ON n.id = dm.node_id AND n.project_id = c.project_id
            LEFT JOIN claim_score_map csm ON csm.claim_id = c.id
            WHERE c.project_id = %s
        ),
        per_node AS (
            -- Hub guard: each visited node contributes at most ``quota``
            -- claims. A claim connecting to several nodes keeps every route,
            -- so being demoted by one hub does not silently erase it. Lane 11:
            -- a citation hub taking part in the seeding exception is exempt
            -- from that guard; relevance (not claim id) orders its claims.
            SELECT claim_id, connecting_node, depth, score,
                   CASE WHEN %s AND n.node_type = 'citation' THEN 1
                        ELSE ROW_NUMBER() OVER (
                            PARTITION BY connecting_node
                            ORDER BY score DESC, depth, claim_id
                        ) END AS node_rank
            FROM connections
            JOIN nodes n ON n.id = connecting_node AND n.project_id = %s
        )
        SELECT c.id, c.text, d.source_uri, cn.status,
               MAX(pn.score) AS best_score, MIN(pn.depth) AS best_depth
        FROM per_node pn
        JOIN claims c ON c.id = pn.claim_id AND c.project_id = %s
        JOIN nodes cn ON cn.id = c.citation_node_id AND cn.project_id = c.project_id
        JOIN documents d ON d.id = c.document_id AND d.project_id = c.project_id
        WHERE cn.status <> ALL(%s::text[])
          AND pn.node_rank <= %s
        GROUP BY c.id, c.text, d.source_uri, cn.status
        ORDER BY best_score DESC, best_depth, c.id
        LIMIT %s
        """,
        (
            node_ids,
            depth_values,
            score_values,
            project_id,
            list(EXCLUDED_NODE_STATUSES),
            claim_ids,
            claim_score_values,
            list(EXCLUDED_NODE_STATUSES),
            project_id,
            hub_quota_exempt,
            project_id,
            project_id,
            list(EXCLUDED_NODE_STATUSES),
            quota,
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
    scores: Mapping[UUID, float] | None = None,
    per_node_quota: int | None = None,
) -> list[ClaimRecord]:
    """Claims connected to the visited nodes, best-match-first, deterministic, capped.

    ``per_node_quota`` defaults to ``None`` (effective no quota); only
    ``answer_question`` passes an explicit quota (:data:`DEFAULT_NODE_QUOTA`).
    """

    if depths is None:
        seeds = set(seed_node_ids_)
        depths = {node_id: (0 if node_id in seeds else 1) for node_id in list(visited_node_ids)}
    claims, _ = _gather_claims_page(
        conn, project_id, depths, cap=cap, scores=scores, per_node_quota=per_node_quota
    )
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
    hub_claim_ranking: bool = DEFAULT_HUB_CLAIM_RANKING,
) -> AnswerResult:
    """Answer one question from the compiled graph using a single drafter call.

    With ``hub_claim_ranking`` (the default), visited citation hubs join claim
    gathering with a nonzero match score and their attached claims are ranked
    by the metered local embedder at query time; no schema change, nothing
    persisted, deterministic (score, depth, id) ordering. Set it to ``False``
    to restore the W1-only retrieval path for A/B tests.
    """

    if not question.strip():
        raise ValueError("question must not be empty")
    started = time.perf_counter()

    try:
        vector = embedder.embed([question])[0]
        scored_seeds = seed_node_ids_with_scores(
            conn, project_id, vector, question, ann_k=ann_k, fts_k=fts_k
        )
        seeds = [node_id for node_id, _ in scored_seeds]
        graph = k_hop_expansion(conn, project_id, seeds, hops=hops)
    except psycopg.Error as exc:
        raise QARetrievalError(f"retrieval failed: {exc}") from exc
    except (IndexError, ValueError) as exc:
        raise QARetrievalError(f"question embedding failed: {exc}") from exc

    depths: dict[UUID, int] = {node_id: 0 for node_id in seeds}
    for hit in graph:
        depths[hit.node_id] = min(depths.get(hit.node_id, hit.depth), hit.depth)
    # R6: a claim inherits the strongest match score of the visited nodes that
    # connect to it. Non-seed graph nodes carry 0 until they are also seeds.
    scores: dict[UUID, float] = {node_id: 0.0 for node_id in depths}
    scores.update(dict(scored_seeds))
    try:
        claim_scores: dict[UUID, float] | None = None
        if hub_claim_ranking:
            # (a) A citation hub barred from seeding may still enter claim
            # gathering directly, carrying the match of its CURRENT version.
            for hub_id, score in citation_hub_scores(conn, project_id, vector).items():
                if hub_id in depths:
                    scores[hub_id] = max(scores.get(hub_id, 0.0), score)
            # (b) Rank hub-attached claims by query-time text relevance using
            # the same in-process, metered local embedder ($0, nothing stored).
            claim_scores = claim_text_scores(
                embedder, vector, hub_attached_claim_texts(conn, project_id)
            )
        claims, truncated = _gather_claims_page(
            conn,
            project_id,
            depths,
            cap=claim_cap,
            scores=scores,
            per_node_quota=DEFAULT_NODE_QUOTA,
            claim_scores=claim_scores,
            hub_quota_exempt=hub_claim_ranking,
        )
    except psycopg.Error as exc:
        raise QARetrievalError(f"claim gathering failed: {exc}") from exc

    prompt_claims = [
        PromptClaim(ref=claim.ref, text=claim.text, document_slug=claim.document_slug)
        for claim in claims
    ]
    # Ruling 22: prose keeps the short ref; the structured contract carries the
    # full UUID. The mapping is never shown to the model (frozen prompt).
    ref_to_uuid = {claim.ref: str(claim.claim_id) for claim in claims}
    payload = _compose_answer(
        drafter, build_user_message(question, prompt_claims), max_retries=max_retries
    )
    verified: VerificationResult = verify_citations(payload, prompt_claims, claim_ids=ref_to_uuid)

    return AnswerResult(
        answer=verified.answer,
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
