"""Lane 11 read-only probe for the q05 citation-hub recall defect.

This is a probe *script*, not a pytest test: it is only run when invoked
explicitly with ``python tests/qa_l11_probe.py``. It targets the corpus DB by
DSN override (the main worktree's ``.env`` with the database name swapped to
``wikimind_live_l3``), opens a ``default_transaction_read_only`` connection,
and performs NO writes and NO Gemini/OpenRouter calls. The only model used is
the in-process local embedder (BGE-small), metered at ``embed.query`` ($0).

It measures seeds -> visited -> gathered, the golden-claim ranks, and the
cut count, both before the candidate mechanism and after it. Runtime stdout
is evidence and is NOT committed (Ruling 18); this script is the committed
artifact.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from wikimind.index.retrieve import k_hop_expansion
from wikimind.llm import InMemorySink
from wikimind.llm.cost import CostMeter
from wikimind.llm.embedder import SentenceTransformerEmbedder
from wikimind.llm.protocols import Embedder
from wikimind.prices import EMBED_MODEL
from wikimind.qa.pipeline import (
    CITATION_NODE_TYPE,
    DEFAULT_CLAIM_CAP,
    DEFAULT_NODE_QUOTA,
    _gather_claims_page,
    _unit_interval,
    citation_hub_scores,
    claim_text_scores,
    hub_attached_claim_texts,
    seed_node_ids_with_scores,
)

os.environ.setdefault("HF_HUB_OFFLINE", "1")

ENV_PATH = Path(r"C:\Users\mianq\Projects\WikiMind\.env")
PROJECT = "v0-fixtures"


def live_dsn() -> str:
    """Build the corpus DSN without ever printing it (Ruling 15/21)."""
    dsn = None
    for raw in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.startswith("WIKIMIND_DB="):
            dsn = line.split("=", 1)[1].strip().strip("'\"")
            break
    if not dsn or "/wikimind_l0" not in dsn:
        raise RuntimeError("main .env WIKIMIND_DB is missing or not the l0 DSN")
    live = dsn.replace("/wikimind_l0", "/wikimind_live_l3")
    if live == dsn:
        raise RuntimeError("DSN override did not change the database")
    return live


# Probe questions. q12/q13 are the two absence cases (Ruling 25(2)).
PROBE = [
    {
        "id": "q01",
        "question": (
            "What network architecture does Attention Is All You Need propose, "
            "and what does it dispense with?"
        ),
        "golden_prefixes": ["4e3b59dd"],
        "absence_terms": [],
    },
    {
        "id": "q02",
        "question": (
            "What BLEU score does the Transformer achieve on the WMT 2014 "
            "English-to-German translation task, and how does it compare with prior results?"
        ),
        "golden_prefixes": ["cc28fdac", "e2c686bf"],
        "absence_terms": [],
    },
    {
        "id": "q05",
        "question": (
            "Which mechanism counteracts the reduced effective resolution caused "
            "by averaging attention-weighted positions?"
        ),
        "golden_prefixes": ["2aa01a08", "419650c5", "6e874a61", "aafa2445", "c3d3a8a2"],
        "absence_terms": [],
    },
    {
        "id": "q12",
        "question": (
            "What real-world analogy does the GAN paper use to explain the "
            "generative and discriminative models?"
        ),
        "golden_prefixes": [],
        "absence_terms": ["counterfeiter", "police", "fake currency"],
    },
    {
        "id": "q13",
        "question": (
            "Which downsides of large pre-trained language models motivate the RAG approach?"
        ),
        "golden_prefixes": [],
        "absence_terms": ["expand or revise", "provenance"],
    },
]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _project_id(conn) -> UUID:
    return conn.execute("SELECT id FROM projects WHERE name=%s", (PROJECT,)).fetchone()["id"]


def _retrieve(conn, embedder: Embedder, question: str):
    vector = embedder.embed([question])[0]
    scored_seeds = seed_node_ids_with_scores(conn, _project_id(conn), vector, question)
    seeds = [node_id for node_id, _ in scored_seeds]
    graph = k_hop_expansion(conn, _project_id(conn), seeds, hops=1)
    depths: dict[UUID, int] = {node_id: 0 for node_id in seeds}
    for hit in graph:
        depths[hit.node_id] = min(depths.get(hit.node_id, hit.depth), hit.depth)
    scores: dict[UUID, float] = {node_id: 0.0 for node_id in depths}
    scores.update(dict(scored_seeds))
    return vector, seeds, graph, depths, scores


def _hub_version_vectors(conn) -> dict[UUID, list[float]]:
    """Stored CURRENT-version embeddings for citation hubs (read-only)."""
    rows = conn.execute(
        """
        SELECT n.id, e.vec::text AS vec
        FROM nodes n
        JOIN embeddings e ON e.node_version_id = n.current_version_id AND e.project_id = n.project_id
        WHERE n.project_id = %s AND n.node_type = %s AND e.model = %s
        """,
        (_project_id(conn), CITATION_NODE_TYPE, EMBED_MODEL),
    ).fetchall()
    out: dict[UUID, list[float]] = {}
    for row in rows:
        vec = row["vec"].strip("[]").split(",")
        out[row["id"]] = [float(x) for x in vec]
    return out


def _claim_texts(conn) -> dict[UUID, str]:
    rows = conn.execute(
        "SELECT id, text FROM claims WHERE project_id=%s", (_project_id(conn),)
    ).fetchall()
    return {row["id"]: row["text"] for row in rows}


def _claim_hub_links(conn) -> dict[UUID, set[UUID]]:
    rows = conn.execute(
        """
        SELECT c.id AS claim_id, n.id AS hub_id
        FROM claims c
        JOIN nodes n ON n.id = c.citation_node_id AND n.project_id = c.project_id
        WHERE c.project_id = %s AND n.node_type = %s
        """,
        (_project_id(conn), CITATION_NODE_TYPE),
    ).fetchall()
    out: dict[UUID, set[UUID]] = {}
    for row in rows:
        out.setdefault(row["claim_id"], set()).add(row["hub_id"])
    return out


def _gather_with_hub_scores(
    conn,
    depths: Mapping[UUID, int],
    scores: Mapping[UUID, float],
    claim_scores: Mapping[UUID, float],
    *,
    cap: int,
    per_node_quota: int,
    hub_quota_exempt: bool = False,
):
    """Candidate mechanism: per-claim text scores break hub-node ties.

    Mirrors _gather_claims_page but, for a connecting node that is a citation
    hub, uses the claim's query-time text score instead of the node's (zero)
    retrieval score. All other connecting nodes keep their retrieval score.
    Determinism is preserved by (score DESC, depth, claim id) everywhere.
    """
    node_ids = list(depths)
    depth_values = [depths[n] for n in node_ids]
    score_values = [_unit_interval(scores.get(n, 0.0)) for n in node_ids]
    claim_ids = list(claim_scores)
    claim_score_values = [_unit_interval(claim_scores[c]) for c in claim_ids]
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
                   SELECT 1 FROM edge_sources es
                   JOIN edges e ON e.id = es.edge_id AND e.project_id = es.project_id
                   WHERE es.project_id = c.project_id AND es.claim_id = c.id
                     AND (e.src_node = dm.node_id OR e.dst_node = dm.node_id)
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
            SELECT claim_id, connecting_node, depth, score,
                   CASE WHEN %s AND n.node_type = 'citation' THEN 1
                        ELSE ROW_NUMBER() OVER (
                            PARTITION BY connecting_node ORDER BY score DESC, depth, claim_id
                        ) END AS node_rank
            FROM connections
            JOIN nodes n ON n.id = connecting_node AND n.project_id = %s
        )
        SELECT c.id, MAX(pn.score) AS best_score, MIN(pn.depth) AS best_depth
        FROM per_node pn
        JOIN claims c ON c.id = pn.claim_id AND c.project_id = %s
        JOIN nodes cn ON cn.id = c.citation_node_id AND cn.project_id = c.project_id
        WHERE cn.status <> ALL(%s::text[]) AND pn.node_rank <= %s
        GROUP BY c.id
        ORDER BY best_score DESC, best_depth, c.id
        LIMIT %s
        """,
        (
            node_ids,
            depth_values,
            score_values,
            _project_id(conn),
            ["archived", "retracted"],
            claim_ids,
            claim_score_values,
            ["archived", "retracted"],
            _project_id(conn),
            hub_quota_exempt,
            _project_id(conn),
            _project_id(conn),
            ["archived", "retracted"],
            per_node_quota,
            cap + 1,
        ),
    ).fetchall()
    truncated = len(rows) > cap
    return [row["id"] for row in rows[:cap]], truncated


def _measure(
    conn,
    embedder,
    entry,
    after: bool,
    hub_vectors,
    claim_texts,
    claim_hubs,
    hub_quota_exempt: bool = False,
    production: bool = False,
):
    question = entry["question"]
    vector, seeds, _graph, depths, scores = _retrieve(conn, embedder, question)
    golden = {
        next(cid for cid in claim_texts if str(cid).startswith(prefix)): prefix
        for prefix in entry["golden_prefixes"]
    }
    all_claim_ids = list(claim_texts)

    if not after:
        claims, truncated = _gather_claims_page(
            conn,
            _project_id(conn),
            depths,
            cap=DEFAULT_CLAIM_CAP,
            scores=scores,
            per_node_quota=DEFAULT_NODE_QUOTA,
        )
        gathered = [c.claim_id for c in claims]
    elif production:
        # The exact production path (no drafter): production helpers + the
        # production _gather_claims_page, so this proves implementation parity.
        for hub_id, score in citation_hub_scores(conn, _project_id(conn), vector).items():
            if hub_id in depths:
                scores[hub_id] = max(scores.get(hub_id, 0.0), score)
        prod_claim_scores = claim_text_scores(
            embedder, vector, hub_attached_claim_texts(conn, _project_id(conn))
        )
        prod_claims, truncated = _gather_claims_page(
            conn,
            _project_id(conn),
            depths,
            cap=DEFAULT_CLAIM_CAP,
            scores=scores,
            per_node_quota=DEFAULT_NODE_QUOTA,
            claim_scores=prod_claim_scores,
            hub_quota_exempt=True,
        )
        gathered = [c.claim_id for c in prod_claims]
    else:
        # (a) hub seeding exception: give visited citation hubs a nonzero score.
        for hub_id, vec in hub_vectors.items():
            if hub_id in depths and hub_id not in scores:
                scores[hub_id] = 0.0
            if hub_id in depths:
                scores[hub_id] = max(scores[hub_id], _unit_interval((_cosine(vector, vec) + 1) / 2))
        # (b) query-time claim-text ranking for hub-attached claims only.
        hub_claim_ids = [cid for cid in all_claim_ids if cid in claim_hubs]
        claim_vecs = embedder.embed([claim_texts[cid] for cid in hub_claim_ids])
        claim_scores = {
            cid: _unit_interval((_cosine(vector, vec) + 1) / 2)
            for cid, vec in zip(hub_claim_ids, claim_vecs)
        }
        gathered, truncated = _gather_with_hub_scores(
            conn,
            depths,
            scores,
            claim_scores,
            cap=DEFAULT_CLAIM_CAP,
            per_node_quota=DEFAULT_NODE_QUOTA,
            hub_quota_exempt=hub_quota_exempt,
        )

    ranks = {}
    for cid, prefix in golden.items():
        ranks[prefix] = gathered.index(cid) + 1 if cid in gathered else None
    cut = sum(1 for rank in ranks.values() if rank is None)
    absence_hits = 0
    if entry["absence_terms"]:
        gathered_texts = [claim_texts[cid] for cid in gathered]
        absence_hits = sum(
            1
            for term in entry["absence_terms"]
            if any(term in text.lower() for text in gathered_texts)
        )
    return {
        "id": entry["id"],
        "seeds": len(seeds),
        "visited": len(depths),
        "hubs_visited": sum(1 for n in depths if n in hub_vectors),
        "gathered": len(gathered),
        "truncated": truncated,
        "golden_ranks": ranks,
        "cut": cut,
        "absence_hits": absence_hits,
    }


class _MemoEmbedder:
    """Memoize identical embed calls; only model I/O is skipped, scores unchanged."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner
        self.cache: dict[tuple[str, ...], list[list[float]]] = {}

    @property
    def model(self) -> str:
        return self.inner.model

    def embed(self, texts: list[str]) -> list[list[float]]:
        key = tuple(texts)
        if key not in self.cache:
            self.cache[key] = self.inner.embed(texts)
        return self.cache[key]


def main() -> None:
    meter = CostMeter(InMemorySink())
    embedder = _MemoEmbedder(SentenceTransformerEmbedder(meter=meter, call_site="embed.query"))
    with psycopg.connect(
        live_dsn(), options="-c default_transaction_read_only=on", row_factory=dict_row
    ) as conn:
        project_id = _project_id(conn)
        before_cost = conn.execute(
            "SELECT count(*) FROM cost_events WHERE project_id=%s", (project_id,)
        ).fetchone()["count"]
        hub_vectors = _hub_version_vectors(conn)
        claim_texts = _claim_texts(conn)
        claim_hubs = _claim_hub_links(conn)

        print("mode,question,seeds,visited,hubs,gathered,truncated,golden_ranks,cut,absence_hits")
        variants = [
            ("before", False, False, False),
            ("after-quota", True, False, False),
            ("after-exempt", True, True, False),
            ("production", True, True, True),
        ]
        for mode, after, exempt, production in variants:
            for entry in PROBE:
                m = _measure(
                    conn,
                    embedder,
                    entry,
                    after,
                    hub_vectors,
                    claim_texts,
                    claim_hubs,
                    exempt,
                    production,
                )
                ranks = (
                    ",".join(
                        f"{k}:{v if v is not None else 'CUT'}" for k, v in m["golden_ranks"].items()
                    )
                    or "-"
                )
                print(
                    f"{mode},{m['id']},{m['seeds']},{m['visited']},"
                    f"{m['hubs_visited']},{m['gathered']},{m['truncated']},{ranks},{m['cut']},"
                    f"{m['absence_hits']}"
                )
        after_cost = conn.execute(
            "SELECT count(*) FROM cost_events WHERE project_id=%s", (project_id,)
        ).fetchone()["count"]
        print(f"cost_events_delta={after_cost - before_cost}")
        non_embed = [e for e in meter.events if e.model != EMBED_MODEL]
        print(f"non_embed_meter_events={len(non_embed)}")
        print("shadow_total_usd=" + f"{sum(e.shadow_cost_usd for e in meter.events):.6f}")


if __name__ == "__main__":
    main()
