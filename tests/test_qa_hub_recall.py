"""Lane 11: citation-hub seeding exception and query-time claim ranking.

The defect (Ruling 26): q05's five golden claims attach only to the attention
citation hub, whose retrieval score is 0.0 and whose per-node quota is 8, so
all five are cut. These tests pin the human-directed mechanism: a visited
citation hub joins claim gathering with a nonzero, query-time rank and is
exempt from the per-node quota. A/B (``hub_claim_ranking=False``) restores W1.

No keys, no network: the local embedder is a controllable fake and the drafter
is the scripted FakeLLM.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from index_seed import basis_vector, seed_edge, seed_embedding, seed_node, seed_node_version

from wikimind.llm import CostMeter, FakeLLM, InMemorySink
from wikimind.prices import EMBED_MODEL
from wikimind.qa.pipeline import (
    DEFAULT_CLAIM_CAP,
    DEFAULT_HUB_CLAIM_RANKING,
    DEFAULT_NODE_QUOTA,
    answer_question,
    claim_text_scores,
)


class KeywordEmbedder:
    """Deterministic stand-in: 'relevant' texts map to the query basis vector."""

    model = EMBED_MODEL

    def __init__(self, meter: CostMeter) -> None:
        self.meter = meter

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.meter.record(call_site="embed.query", model=EMBED_MODEL, in_tokens=1)
        return [basis_vector(0) if "relevant" in text else basis_vector(100) for text in texts]


def _seed_hub_case(conn, project_name: str):
    """One seed concept, one citation hub, 12 claims; the 2 relevant ones have
    the highest claim ids so the W1 quota necessarily cuts them."""

    project_id = conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id", (project_name,)
    ).fetchone()[0]
    doc_id = conn.execute(
        "INSERT INTO documents (project_id, sha256, source_uri, parser, prompt_version, "
        "model, schema_version) VALUES (%s, %s, %s, 'text', 'compile-v0', 'fake-llm', 'v1') "
        "RETURNING id",
        (project_id, UUID(int=1).hex, "file:///attention.pdf"),
    ).fetchone()[0]

    seed = seed_node(conn, project_id, "hub-seed", node_type="concept")
    seed_version = seed_node_version(conn, project_id, seed, "relevant mechanism concept")
    seed_embedding(conn, project_id, seed_version, EMBED_MODEL, basis_vector(0))

    hub = seed_node(conn, project_id, "source:hub", node_type="citation")
    hub_version = seed_node_version(conn, project_id, hub, "attention paper")
    seed_embedding(conn, project_id, hub_version, EMBED_MODEL, basis_vector(100))
    seed_edge(conn, project_id, seed, hub)

    # Unique across tests in the shared template DB; low bits keep the two
    # relevant claim ids the largest *within this project*.
    base = (uuid4().int >> 8) << 8
    claim_ids: list[UUID] = []
    for i in range(12):
        claim_id = UUID(int=base + i + 1)
        text = "relevant claim" if i >= 10 else f"distractor claim {i}"
        channel = f"hub-ch-{i}"
        conn.execute(
            "INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text) "
            "VALUES (%s, %s, %s, 'title', 0, %s)",
            (channel, project_id, doc_id, text),
        )
        conn.execute(
            "INSERT INTO claims (id, project_id, citation_node_id, channel_id, document_id, "
            "anchor_quote, anchor_span, text) VALUES (%s, %s, %s, %s, %s, %s, '{}'::jsonb, %s)",
            (claim_id, project_id, hub, channel, doc_id, text, text),
        )
        claim_ids.append(claim_id)
    return project_id, hub, claim_ids[10], claim_ids[11]


def _ask(conn, project_id, *, ranking: bool):
    meter = CostMeter(InMemorySink())
    fake = FakeLLM([{"answer": "test", "sufficient": False, "citations": []}], meter=meter)
    return answer_question(
        conn,
        project_id,
        "relevant mechanism?",
        embedder=KeywordEmbedder(meter),
        drafter=fake,
        meter=meter,
        hub_claim_ranking=ranking,
    )


def test_hub_ranking_default_is_pinned() -> None:
    """The exception is on by default; W1 quotas stay pinned."""

    assert DEFAULT_HUB_CLAIM_RANKING is True
    assert DEFAULT_CLAIM_CAP == 150
    assert DEFAULT_NODE_QUOTA == 8


def test_hub_attached_claims_ranked_into_cap(db_conn) -> None:
    """Relevant hub claims survive the quota and lead the gathered set."""

    project_id, _hub, relevant_a, relevant_b = _seed_hub_case(db_conn, "qa-l11-ranked")
    result = _ask(db_conn, project_id, ranking=True)
    gathered = [claim.claim_id for claim in result.claims]
    assert relevant_a in gathered
    assert relevant_b in gathered
    assert gathered[:2] == [relevant_a, relevant_b]
    assert len(gathered) == 12


def test_hub_ranking_off_restores_w1_quota(db_conn) -> None:
    """A/B: with the exception off, the two high-id claims are cut again."""

    project_id, _hub, relevant_a, relevant_b = _seed_hub_case(db_conn, "qa-l11-w1")
    result = _ask(db_conn, project_id, ranking=False)
    gathered = [claim.claim_id for claim in result.claims]
    assert relevant_a not in gathered
    assert relevant_b not in gathered
    assert len(gathered) == DEFAULT_NODE_QUOTA


def test_hub_ranking_is_deterministic(db_conn) -> None:
    """Identical inputs give identical ordered claim ids."""

    project_id, _hub, _a, _b = _seed_hub_case(db_conn, "qa-l11-determinism")
    first = [claim.claim_id for claim in _ask(db_conn, project_id, ranking=True).claims]
    second = [claim.claim_id for claim in _ask(db_conn, project_id, ranking=True).claims]
    assert first == second


def test_claim_text_scores_use_embed_query_call_site(db_conn) -> None:
    """Claim ranking is metered at embed.query and costs $0 shadow."""

    meter = CostMeter(InMemorySink())
    embedder = KeywordEmbedder(meter)
    scores = claim_text_scores(
        embedder, basis_vector(0), {UUID(int=1): "relevant claim", UUID(int=2): "other"}
    )
    assert scores[UUID(int=1)] > scores[UUID(int=2)]
    assert all(event.call_site == "embed.query" for event in meter.events)
    assert all(event.shadow_cost_usd == 0.0 for event in meter.events)
