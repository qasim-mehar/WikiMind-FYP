"""Retrieval substrates over the schema: ANN, k-hop expansion, full-text, hybrid.

Thin, library-only building blocks for the later QA lane. Deliberately no
ranking-fusion policy and no user-facing surface: how the three signals are
combined into answers is post-Gate-B scope. Every query scopes by
``project_id`` per schema.md, every version-bearing path additionally scopes
to a node's CURRENT version (superseded versions never surface), and every
list is returned in a deterministic order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import psycopg

from ..prices import EMBED_MODEL
from .store import format_vector


@dataclass(frozen=True, slots=True)
class AnnHit:
    """One vector-ANN result; ``similarity`` is cosine similarity in [-1, 1]."""

    node_id: UUID
    node_version_id: UUID
    similarity: float


@dataclass(frozen=True, slots=True)
class FtsHit:
    """One full-text result over node version prose."""

    node_id: UUID
    node_version_id: UUID
    version: int
    rank: float


@dataclass(frozen=True, slots=True)
class GraphHit:
    """One node reached by graph expansion; ``depth`` is the minimum hop count."""

    node_id: UUID
    depth: int
    slug: str
    node_type: str
    status: str


@dataclass(frozen=True, slots=True)
class HybridHits:
    """The three substrate result lists plus their union, keyed by node id.

    ``node_ids`` keeps first-seen order across ``ann``, then ``fts``, then
    ``graph``. No cross-source ranking is applied.
    """

    ann: list[AnnHit]
    fts: list[FtsHit]
    graph: list[GraphHit]
    node_ids: list[UUID]


def ann_search(
    conn: psycopg.Connection,
    project_id: UUID,
    query_vector: list[float],
    *,
    k: int = 10,
    model: str = EMBED_MODEL,
) -> list[AnnHit]:
    """Top-k node versions by cosine similarity, served by the HNSW index.

    Only embeddings produced by ``model`` are searched, and only those on a
    node's CURRENT version: superseded versions never surface, even if an
    embedding row for them still exists. Ordered by similarity descending,
    ties broken by node version id.

    Planner caveat: the current-version filter is applied at scan time, so
    on Postgres builds without iterative HNSW scans a corpus with many
    superseded versions can return fewer than ``k`` current hits.
    Correctness is unaffected; completeness at scale is the caveat.
    """

    if k < 1:
        raise ValueError("k must be positive")
    literal = format_vector(query_vector)
    rows = conn.execute(
        """
        SELECT nv.node_id, e.node_version_id, 1 - (e.vec <=> %s::vector) AS similarity
        FROM embeddings e
        JOIN node_versions nv ON nv.id = e.node_version_id
        JOIN nodes n ON n.id = nv.node_id
        WHERE e.project_id = %s AND e.model = %s
          AND n.current_version_id = nv.id
        ORDER BY e.vec <=> %s::vector, e.node_version_id
        LIMIT %s
        """,
        (literal, project_id, model, literal, k),
    ).fetchall()
    return [
        AnnHit(node_id=row[0], node_version_id=row[1], similarity=float(row[2])) for row in rows
    ]


def k_hop_expansion(
    conn: psycopg.Connection,
    project_id: UUID,
    node_ids: Sequence[UUID],
    *,
    hops: int = 1,
) -> list[GraphHit]:
    """Nodes within ``hops`` edges of the seeds, following edges in both directions.

    Depth is the minimum hop count from any seed; seeds come back at depth 0.
    Seeds outside ``project_id`` are dropped, and the traversal only follows
    edges of the same project, so cross-project references cannot leak.
    Ordered by (depth, node id).
    """

    if hops < 0:
        raise ValueError("hops must not be negative")
    seeds = sorted(set(node_ids))
    if not seeds:
        return []

    rows = conn.execute(
        """
        WITH RECURSIVE walk(node_id, depth) AS (
            SELECT n.id, 0
            FROM nodes n
            WHERE n.project_id = %s AND n.id = ANY(%s)
            UNION
            SELECT CASE WHEN e.src_node = w.node_id THEN e.dst_node ELSE e.src_node END,
                   w.depth + 1
            FROM walk w
            JOIN edges e ON e.project_id = %s
                AND (e.src_node = w.node_id OR e.dst_node = w.node_id)
            WHERE w.depth < %s
        )
        SELECT n.id, MIN(w.depth), n.slug, n.node_type, n.status
        FROM walk w
        JOIN nodes n ON n.id = w.node_id AND n.project_id = %s
        GROUP BY n.id, n.slug, n.node_type, n.status
        ORDER BY 2, n.id
        """,
        (project_id, seeds, project_id, hops, project_id),
    ).fetchall()
    return [
        GraphHit(
            node_id=row[0],
            depth=int(row[1]),
            slug=row[2],
            node_type=row[3],
            status=row[4],
        )
        for row in rows
    ]


def fulltext_search(
    conn: psycopg.Connection,
    project_id: UUID,
    query: str,
    *,
    k: int = 10,
) -> list[FtsHit]:
    """Match node version prose with ``websearch_to_tsquery`` over the stored tsvector.

    ``query`` uses Postgres websearch syntax (plain words, ``OR``, quoted
    phrases); it never raises on odd input, it just matches nothing.  Only a
    node's CURRENT version is searched: superseded versions never surface.
    Ordered by ``ts_rank`` descending, ties broken by node version id.
    """

    if k < 1:
        raise ValueError("k must be positive")
    rows = conn.execute(
        """
        SELECT nv.node_id, nv.id, nv.version,
               ts_rank(nv.fts, websearch_to_tsquery('english', %s)) AS score
        FROM node_versions nv
        JOIN nodes n ON n.id = nv.node_id
        WHERE nv.project_id = %s
          AND n.current_version_id = nv.id
          AND nv.fts @@ websearch_to_tsquery('english', %s)
        ORDER BY score DESC, nv.id
        LIMIT %s
        """,
        (query, project_id, query, k),
    ).fetchall()
    return [
        FtsHit(
            node_id=row[0],
            node_version_id=row[1],
            version=int(row[2]),
            rank=float(row[3]),
        )
        for row in rows
    ]


def hybrid_fetch(
    conn: psycopg.Connection,
    project_id: UUID,
    *,
    query_vector: list[float] | None = None,
    query_text: str | None = None,
    seed_node_ids: Sequence[UUID] = (),
    k: int = 10,
    hops: int = 1,
    model: str = EMBED_MODEL,
) -> HybridHits:
    """Run the retrieval substrates and union their results by node id.

    ANN runs when ``query_vector`` is given, full-text when ``query_text`` is
    given, and k-hop expansion always runs, seeded with ``seed_node_ids``
    plus whatever the first two returned. Each result list keeps its own
    ordering; the union in ``node_ids`` is first-seen order across the three
    lists. No scoring or fusion policy is applied here.
    """

    if query_vector is None and query_text is None and not seed_node_ids:
        raise ValueError("hybrid_fetch needs a query_vector, query_text, or seed_node_ids")

    ann_hits = (
        ann_search(conn, project_id, query_vector, k=k, model=model)
        if query_vector is not None
        else []
    )
    fts_hits = fulltext_search(conn, project_id, query_text, k=k) if query_text is not None else []

    seeds: set[UUID] = set(seed_node_ids)
    seeds.update(hit.node_id for hit in ann_hits)
    seeds.update(hit.node_id for hit in fts_hits)
    graph_hits = k_hop_expansion(conn, project_id, seeds, hops=hops) if seeds else []

    union: dict[UUID, None] = {}
    for hit in (*ann_hits, *fts_hits, *graph_hits):
        union.setdefault(hit.node_id, None)

    return HybridHits(
        ann=ann_hits,
        fts=fts_hits,
        graph=graph_hits,
        node_ids=list(union),
    )
