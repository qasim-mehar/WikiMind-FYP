"""DB-backed QA tests: seed scoping (F2), status filtering (F3), ranking, E2E."""

from __future__ import annotations

from uuid import UUID, uuid4

from index_seed import basis_vector, seed_edge, seed_embedding, seed_node, seed_node_version

from wikimind.index.retrieve import k_hop_expansion
from wikimind.llm import CostMeter, FakeLLM, InMemorySink
from wikimind.prices import EMBED_MODEL
from wikimind.qa.pipeline import (
    DEFAULT_CLAIM_CAP,
    _gather_claims_page,
    answer_question,
    document_slug,
    gather_claims,
    seed_node_ids,
)
from wikimind.qa.prompts import CALL_SITE_EMBED, claim_ref


def _project(conn) -> UUID:
    return conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id", (f"qa-{uuid4().hex[:8]}",)
    ).fetchone()[0]


def _document(conn, project_id: UUID, slug_seed: str) -> tuple[UUID, str, str]:
    sha = uuid4().hex + slug_seed
    uri = f"file:///fixtures/{slug_seed}.txt"
    doc_id = conn.execute(
        """
        INSERT INTO documents
            (project_id, sha256, source_uri, parser, prompt_version, model, schema_version)
        VALUES (%s, %s, %s, 'text', 'compile-v0', 'fake-llm', 'v1')
        RETURNING id
        """,
        (project_id, sha, uri),
    ).fetchone()[0]
    return doc_id, sha, uri


def _claim(
    conn,
    project_id: UUID,
    document_id: UUID,
    citation_node_id: UUID,
    text: str,
) -> UUID:
    channel_id = f"qa-channel-{uuid4().hex}"
    conn.execute(
        "INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text) "
        "VALUES (%s, %s, %s, 'title', 0, %s)",
        (channel_id, project_id, document_id, text),
    )
    return conn.execute(
        "INSERT INTO claims (project_id, citation_node_id, channel_id, document_id, "
        "anchor_quote, anchor_span, text) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, %s) "
        "RETURNING id",
        (project_id, citation_node_id, channel_id, document_id, text, text),
    ).fetchone()[0]


def _seed_graph(conn):
    """A concept seed and a citation node that dominates ANN and full-text."""

    project_id = _project(conn)
    document_id, sha, uri = _document(conn, project_id, "dominant")
    citation_slug = f"source:{sha}"
    big_text = " ".join(["quantum"] * 400)
    citation = seed_node(conn, project_id, citation_slug, node_type="citation")
    citation_version = seed_node_version(conn, project_id, citation, big_text)
    concept = seed_node(conn, project_id, "quantum-mechanics", node_type="concept")
    concept_version = seed_node_version(conn, project_id, concept, "quantum mechanics overview")

    seed_embedding(conn, project_id, citation_version, EMBED_MODEL, basis_vector(0))
    seed_embedding(conn, project_id, concept_version, EMBED_MODEL, basis_vector(1))
    edge = seed_edge(conn, project_id, concept, citation)
    return project_id, document_id, citation, concept, edge, citation_slug, uri


class FixedEmbedder:
    """Stub matching the Embedder protocol with a chosen vector."""

    model = EMBED_MODEL

    def __init__(self, vector: list[float], meter: CostMeter | None = None) -> None:
        self.vector = vector
        self.meter = meter

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.meter is not None:
            self.meter.record(
                call_site=CALL_SITE_EMBED,
                model=EMBED_MODEL,
                in_tokens=sum(max(1, len(text) // 4) for text in texts),
            )
        return [list(self.vector) for _ in texts]


def test_citation_node_never_a_seed_but_stays_traversable(db_conn) -> None:
    project_id, _, citation, concept, _, _, _ = _seed_graph(db_conn)

    seeds = seed_node_ids(db_conn, project_id, basis_vector(0), "quantum")

    assert citation not in seeds
    assert concept in seeds
    graph = k_hop_expansion(db_conn, project_id, seeds, hops=1)
    assert citation in {hit.node_id for hit in graph}


def test_more_than_k_dominant_citation_nodes_still_yield_seeds(db_conn) -> None:
    """F2: the type filter runs in SQL before the LIMIT, not after it."""

    project_id = _project(conn := db_conn)
    document_id, _, _ = _document(conn, project_id, "dominators")
    # 25 citation nodes all closer to the query than the single concept node.
    for index in range(25):
        node = seed_node(conn, project_id, f"source:dominator-{index}", node_type="citation")
        version = seed_node_version(conn, project_id, node, "quantum " * 50)
        seed_embedding(conn, project_id, version, EMBED_MODEL, basis_vector(0))
        _claim(conn, project_id, document_id, node, f"dominant claim {index}")
    concept = seed_node(conn, project_id, "quantum-concept", node_type="concept")
    cversion = seed_node_version(conn, project_id, concept, "quantum concept")
    seed_embedding(conn, project_id, cversion, EMBED_MODEL, basis_vector(1))

    seeds = seed_node_ids(conn, project_id, basis_vector(0), "quantum", ann_k=5, fts_k=5)

    assert seeds == [concept]


def test_seed_order_is_ann_then_fts_and_deduplicated(db_conn) -> None:
    project_id, _, _, concept, _, _, _ = _seed_graph(db_conn)
    seeds = seed_node_ids(db_conn, project_id, basis_vector(1), "quantum")
    assert seeds == [concept]


def test_gather_claims_uses_both_provenance_paths_and_canonical_slug(db_conn) -> None:
    project_id, document_id, citation, _, edge, _, uri = _seed_graph(db_conn)
    by_citation = _claim(
        db_conn, project_id, document_id, citation, "Claim stored on citation page"
    )
    by_edge = _claim(db_conn, project_id, document_id, citation, "Claim cited by graph edge")
    db_conn.execute(
        "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
        (project_id, edge, by_edge),
    )

    claims = gather_claims(db_conn, project_id, [citation])

    refs = {claim.claim_id for claim in claims}
    assert {by_citation, by_edge} <= refs
    assert all(claim.document_slug == document_slug(uri) == "dominant" for claim in claims)
    assert all(claim.ref.startswith("c-") for claim in claims)


def test_claim_rank_is_shallowest_connecting_node(db_conn) -> None:
    """M1: seed-adjacent claims sort before deeper k-hop claims."""

    conn = db_conn
    project_id = _project(conn)
    document_id, _, _ = _document(conn, project_id, "ranking")
    seed = seed_node(conn, project_id, "seed-node", node_type="concept")
    near_citation = seed_node(conn, project_id, "source:near", node_type="citation")
    mid = seed_node(conn, project_id, "mid-node", node_type="concept")
    far_citation = seed_node(conn, project_id, "source:far", node_type="citation")
    seed_edge(conn, project_id, seed, near_citation)
    seed_edge(conn, project_id, seed, mid)
    seed_edge(conn, project_id, mid, far_citation)
    near_claim = _claim(conn, project_id, document_id, near_citation, "near claim")
    far_claim = _claim(conn, project_id, document_id, far_citation, "far claim")

    claims = gather_claims(
        conn,
        project_id,
        [seed, near_citation, mid, far_citation],
        depths={seed: 0, near_citation: 1, mid: 1, far_citation: 2},
        cap=10,
    )

    assert [claim.claim_id for claim in claims] == [near_claim, far_claim]


def test_retracted_and_archived_provenance_is_excluded(db_conn) -> None:
    """F3: retracted/archived nodes never contribute claims."""

    conn = db_conn
    project_id = _project(conn)
    document_id, _, _ = _document(conn, project_id, "statuses")
    active = seed_node(conn, project_id, "source:active", node_type="citation")
    retracted = seed_node(
        conn, project_id, "source:retracted", node_type="citation", status="retracted"
    )
    archived = seed_node(
        conn, project_id, "source:archived", node_type="citation", status="archived"
    )
    stale = seed_node(conn, project_id, "source:stale", node_type="citation", status="stale")
    kept_claim = _claim(conn, project_id, document_id, active, "active claim")
    _claim(conn, project_id, document_id, retracted, "retracted claim")
    _claim(conn, project_id, document_id, archived, "archived claim")
    stale_claim = _claim(conn, project_id, document_id, stale, "stale claim")

    claims = gather_claims(conn, project_id, [active, retracted, archived, stale], cap=10)

    ids = {claim.claim_id for claim in claims}
    assert kept_claim in ids and stale_claim in ids
    assert len(claims) == 2
    by_id = {claim.claim_id: claim for claim in claims}
    assert by_id[kept_claim].node_status == "active"
    assert by_id[stale_claim].node_status == "stale"


def test_claim_connected_only_through_retracted_node_is_excluded(db_conn) -> None:
    """F8: every node on an edge provenance path must be active."""

    conn = db_conn
    project_id = _project(conn)
    document_id, _, _ = _document(conn, project_id, "edgestatus")
    citation = seed_node(conn, project_id, "source:active-page", node_type="citation")
    seed = seed_node(conn, project_id, "edge-seed", node_type="concept")
    retracted = seed_node(
        conn, project_id, "edge-retracted", node_type="concept", status="retracted"
    )
    active_peer = seed_node(conn, project_id, "edge-active", node_type="concept")

    bad_claim = _claim(conn, project_id, document_id, citation, "only a retracted path")
    good_claim = _claim(conn, project_id, document_id, citation, "an all-active path")
    bad_edge = seed_edge(conn, project_id, seed, retracted)
    good_edge = seed_edge(conn, project_id, seed, active_peer)
    for edge, claim_id in ((bad_edge, bad_claim), (good_edge, good_claim)):
        conn.execute(
            "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
            (project_id, edge, claim_id),
        )

    claims = gather_claims(conn, project_id, [seed], cap=10)
    ids = {claim.claim_id for claim in claims}

    assert good_claim in ids
    assert bad_claim not in ids


def test_truncated_flag_only_when_rows_were_dropped(db_conn) -> None:
    """F5c: exactly cap rows is not a truncation."""

    conn = db_conn
    project_id, document_id, citation, _, _, _, _ = _seed_graph(conn)
    for index in range(3):
        _claim(conn, project_id, document_id, citation, f"claim {index}")

    exact, truncated = _gather_claims_page(conn, project_id, {citation: 0}, cap=3)
    assert len(exact) == 3 and truncated is False
    over, truncated = _gather_claims_page(conn, project_id, {citation: 0}, cap=2)
    assert len(over) == 2 and truncated is True


def test_gather_claims_is_deterministic_at_the_cap(db_conn) -> None:
    project_id, document_id, citation, _, _, _, _ = _seed_graph(db_conn)
    ids = [
        _claim(db_conn, project_id, document_id, citation, f"Claim number {i:02d}")
        for i in range(6)
    ]
    first = gather_claims(db_conn, project_id, [citation], cap=3)
    second = gather_claims(db_conn, project_id, [citation], cap=3)
    assert len(first) == 3
    assert [c.claim_id for c in first] == [c.claim_id for c in second]
    assert all(c.claim_id in ids for c in first)


def test_answer_question_end_to_end_on_seeded_db(db_conn) -> None:
    project_id, document_id, citation, concept, edge, _, uri = _seed_graph(db_conn)
    claim_id = _claim(db_conn, project_id, document_id, citation, "The Transformer uses attention.")
    db_conn.execute(
        "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
        (project_id, edge, claim_id),
    )
    meter = CostMeter(InMemorySink())
    ref = claim_ref(claim_id)
    fake = FakeLLM(
        [
            {
                "answer": f"The Transformer uses attention [{ref}].",
                "sufficient": True,
                "citations": [{"claim_id": ref, "document_slug": document_slug(uri)}],
            }
        ],
        meter=meter,
    )

    result = answer_question(
        db_conn,
        project_id,
        "quantum",
        embedder=FixedEmbedder(basis_vector(0), meter=meter),
        drafter=fake,
        meter=meter,
    )

    assert citation not in result.seeds
    assert concept in result.seeds
    assert any(claim.claim_id == claim_id for claim in result.claims)
    assert result.sufficient is True
    assert result.claim_cap == DEFAULT_CLAIM_CAP
    assert result.wall_time_s >= 0.0
    assert {event.call_site for event in meter.events} == {"embed.query", "qa.answer"}
    assert result.shadow_cost_usd == meter.total_shadow_cost_usd
    assert result.unverified_removed == 0
    # Ruling 22: the structured citation carries the canonical full UUID;
    # the short ref stays in prose only.
    assert [citation.claim_id for citation in result.citations] == [str(claim_id)]
