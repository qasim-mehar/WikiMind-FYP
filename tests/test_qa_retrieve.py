"""DB-backed QA tests: citation-node exclusion (Q2), claim gathering, end-to-end."""

from __future__ import annotations

from uuid import UUID, uuid4

from index_seed import basis_vector, seed_edge, seed_embedding, seed_node, seed_node_version

from wikimind.index.retrieve import k_hop_expansion
from wikimind.llm import CostMeter, FakeLLM, InMemorySink
from wikimind.prices import EMBED_MODEL
from wikimind.qa.pipeline import (
    DEFAULT_CLAIM_CAP,
    answer_question,
    gather_claims,
    seed_node_ids,
)
from wikimind.qa.prompts import CALL_SITE_EMBED


def _project(conn) -> UUID:
    return conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id", (f"qa-{uuid4().hex[:8]}",)
    ).fetchone()[0]


def _document(conn, project_id: UUID, slug_seed: str) -> tuple[UUID, str]:
    sha = uuid4().hex + slug_seed
    doc_id = conn.execute(
        """
        INSERT INTO documents
            (project_id, sha256, source_uri, parser, prompt_version, model, schema_version)
        VALUES (%s, %s, %s, 'text', 'compile-v0', 'fake-llm', 'v1')
        RETURNING id
        """,
        (project_id, sha, f"file:///fixtures/{slug_seed}.txt"),
    ).fetchone()[0]
    return doc_id, sha


def _claim(
    conn,
    project_id: UUID,
    document_id: UUID,
    citation_node_id: UUID,
    text: str,
) -> UUID:
    channel_id = f"qa-channel-{uuid4().hex}"
    conn.execute(
        """
        INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text)
        VALUES (%s, %s, %s, 'title', 0, %s)
        """,
        (channel_id, project_id, document_id, text),
    )
    return conn.execute(
        """
        INSERT INTO claims
            (project_id, citation_node_id, channel_id, document_id,
             anchor_quote, anchor_span, text)
        VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, %s)
        RETURNING id
        """,
        (project_id, citation_node_id, channel_id, document_id, text, text),
    ).fetchone()[0]


def _seed_graph(conn):
    """A concept seed and a citation node that dominates ANN and full-text."""

    project_id = _project(conn)
    document_id, sha = _document(conn, project_id, "dominant")
    citation_slug = f"source:{sha}"
    big_text = " ".join(["quantum"] * 400)
    citation = seed_node(conn, project_id, citation_slug, node_type="citation")
    citation_version = seed_node_version(conn, project_id, citation, big_text)
    concept = seed_node(conn, project_id, "quantum-mechanics", node_type="concept")
    concept_version = seed_node_version(conn, project_id, concept, "quantum mechanics overview")

    # Both embeddings are current versions; the citation node is aligned with
    # the query vector, so it *would* be the top ANN hit if not filtered.
    seed_embedding(conn, project_id, citation_version, "BAAI/bge-small-en-v1.5", basis_vector(0))
    seed_embedding(conn, project_id, concept_version, "BAAI/bge-small-en-v1.5", basis_vector(1))
    edge = seed_edge(conn, project_id, concept, citation)
    return project_id, document_id, citation, concept, edge, citation_slug


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
    project_id, _, citation, concept, _, _ = _seed_graph(db_conn)

    seeds = seed_node_ids(db_conn, project_id, basis_vector(0), "quantum")

    assert citation not in seeds
    assert concept in seeds
    graph = k_hop_expansion(db_conn, project_id, seeds, hops=1)
    assert citation in {hit.node_id for hit in graph}


def test_seed_order_is_ann_then_fts_and_deduplicated(db_conn) -> None:
    project_id, _, _, concept, _, _ = _seed_graph(db_conn)
    seeds = seed_node_ids(db_conn, project_id, basis_vector(1), "quantum")
    assert seeds == [concept]


def test_gather_claims_uses_both_provenance_paths_and_document_slug(db_conn) -> None:
    project_id, document_id, citation, concept, edge, citation_slug = _seed_graph(db_conn)
    by_citation = _claim(
        db_conn, project_id, document_id, citation, "Claim stored on citation page"
    )
    by_edge = _claim(db_conn, project_id, document_id, citation, "Claim cited by graph edge")
    db_conn.execute(
        "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
        (project_id, edge, by_edge),
    )

    claims = gather_claims(db_conn, project_id, [concept, citation], seed_node_ids_=[concept])

    refs = {claim.claim_id for claim in claims}
    assert {by_citation, by_edge} <= refs
    assert all(claim.document_slug == citation_slug for claim in claims)
    assert all(claim.ref.startswith("c-") for claim in claims)


def test_gather_claims_is_capped_and_deterministic(db_conn) -> None:
    project_id, document_id, citation, _, _, _ = _seed_graph(db_conn)
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
    project_id, document_id, citation, concept, edge, citation_slug = _seed_graph(db_conn)
    claim_id = _claim(db_conn, project_id, document_id, citation, "The Transformer uses attention.")
    db_conn.execute(
        "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
        (project_id, edge, claim_id),
    )
    meter = CostMeter(InMemorySink())
    fake = FakeLLM(
        [
            {
                "answer": "The Transformer uses attention [c-x].",
                "sufficient": True,
                "citations": [{"claim_id": "c-x", "document_slug": citation_slug}],
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
    assert {event.call_site for event in meter.events} == {"qa.embed", "qa.answer"}
    assert result.shadow_cost_usd == meter.total_shadow_cost_usd
