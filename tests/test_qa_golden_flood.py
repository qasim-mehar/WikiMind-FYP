"""Gate C: golden-flood guard for the Lane 11 citation-hub seeding exception.

Ruling 26 made a visited citation hub join claim gathering with a query-time
rank and a quota exemption so q05's hub-attached golden claims survive.  That
exception must not invert the answer: on the golden query the hub's *seeded*
claims may be retrieved, but they must stay a minority of what is actually
cited.  The test seeds 5 golden claims on the retrieved concept and 12 flood
claims on the hub, then uses a scripted, selective drafter (the realistic case:
an answer cites a handful of the top-ranked claims).  With the exception on,
the golden claims lead the prompt and own the citations; if hub claims ever
out-rank them, the guard fails.

No keys, no network, no live corpus; the embedder is a controllable fake.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID, uuid4

from index_seed import basis_vector, seed_edge, seed_embedding, seed_node, seed_node_version

from wikimind.llm import CostMeter, FakeLLM, InMemorySink
from wikimind.prices import EMBED_MODEL
from wikimind.qa.pipeline import DEFAULT_CLAIM_CAP, answer_question

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "qa_questions.json"
GOLDEN_ID = "attn-multihead"  # Ruling 26 q05
GOLDEN_COUNT = 5
FLOOD_COUNT = 12
CITED_LIMIT = 5
_CLAIM_TAG = re.compile(r'<claim id="([^"]+)" document="([^"]+)">')


def _golden_question() -> str:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return next(entry["question"] for entry in data["questions"] if entry["id"] == GOLDEN_ID)


class GoldenEmbedder:
    """Golden text maps to the query axis; flood text and the hub do not."""

    model = EMBED_MODEL

    def __init__(self, meter: CostMeter) -> None:
        self.meter = meter

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.meter.record(call_site="embed.query", model=EMBED_MODEL, in_tokens=1)
        return [
            basis_vector(0)
            if "attention-weighted" in text or "golden claim" in text
            else basis_vector(100)
            for text in texts
        ]


def _claim(conn, project_id, document_id, citation, claim_id: UUID, text: str) -> None:
    channel = f"flood-ch-{claim_id.hex}"
    conn.execute(
        "INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text) "
        "VALUES (%s, %s, %s, 'title', 0, %s)",
        (channel, project_id, document_id, text),
    )
    conn.execute(
        "INSERT INTO claims (id, project_id, citation_node_id, channel_id, document_id, "
        "anchor_quote, anchor_span, text) VALUES (%s, %s, %s, %s, %s, %s, '{}'::jsonb, %s)",
        (claim_id, project_id, citation, channel, document_id, text, text),
    )


def _seed_flood_case(conn, project_name: str):
    project_id = conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id", (project_name,)
    ).fetchone()[0]
    doc_id = conn.execute(
        "INSERT INTO documents (project_id, sha256, source_uri, parser, prompt_version, "
        "model, schema_version) VALUES (%s, %s, %s, 'text', 'compile-v0', 'fake-llm', 'v1') "
        "RETURNING id",
        (project_id, UUID(int=1).hex, "file:///attention.pdf"),
    ).fetchone()[0]

    concept = seed_node(conn, project_id, "golden-mechanism", node_type="concept")
    c_version = seed_node_version(conn, project_id, concept, "relevant mechanism concept")
    seed_embedding(conn, project_id, c_version, EMBED_MODEL, basis_vector(0))

    hub = seed_node(conn, project_id, "source:attention", node_type="citation")
    h_version = seed_node_version(conn, project_id, hub, "attention citation hub")
    seed_embedding(conn, project_id, h_version, EMBED_MODEL, basis_vector(100))
    seed_edge(conn, project_id, concept, hub)

    # Fresh UUIDs: the 12-hex prompt refs must be distinct or machine
    # verification (Ruling 22) collapses several claims into one citation.
    golden_ids: set[UUID] = set()
    flood_ids: set[UUID] = set()
    for i in range(GOLDEN_COUNT):
        claim_id = uuid4()
        _claim(conn, project_id, doc_id, concept, claim_id, f"relevant golden claim {i}")
        golden_ids.add(claim_id)
    for i in range(FLOOD_COUNT):
        claim_id = uuid4()
        _claim(conn, project_id, doc_id, hub, claim_id, f"distractor flood claim {i}")
        flood_ids.add(claim_id)
    return project_id, golden_ids, flood_ids


def _cite_leading(limit: int):
    """A selective drafter: cite the first ``limit`` claims in the prompt."""

    def respond(*, user, **_kwargs):
        cited = _CLAIM_TAG.findall(user)[:limit]
        return {
            "answer": " ".join(f"[{ref}]" for ref, _ in cited) or "No claims provided.",
            "sufficient": bool(cited),
            "citations": [{"claim_id": ref, "document_slug": slug} for ref, slug in cited],
        }

    return respond


def test_golden_flood_guard_defaults_are_pinned() -> None:
    assert DEFAULT_CLAIM_CAP == 150
    assert GOLDEN_ID == "attn-multihead"
    assert "attention-weighted" in _golden_question()


def test_hub_seeded_claims_stay_a_minority_of_cited_set(db_conn) -> None:
    project_id, golden_ids, flood_ids = _seed_flood_case(db_conn, "qa-golden-flood")
    meter = CostMeter(InMemorySink())
    fake = FakeLLM([_cite_leading(CITED_LIMIT)], meter=meter)
    result = answer_question(
        db_conn,
        project_id,
        _golden_question(),
        embedder=GoldenEmbedder(meter),
        drafter=fake,
        meter=meter,
    )

    gathered = {str(claim.claim_id) for claim in result.claims}
    cited = {str(citation.claim_id) for citation in result.citations}
    flood_cited = cited & {str(claim_id) for claim_id in flood_ids}

    # Not vacuous: the hub exception really did surface all flood claims, and
    # the golden claims are the ones the drafter chose to cite.
    assert {str(claim_id) for claim_id in flood_ids} <= gathered
    assert cited, "a golden query must produce citations"
    assert {str(claim_id) for claim_id in golden_ids} <= cited
    assert len(flood_cited) * 2 < len(cited), (
        f"hub-seeded claims flooded the cited set: {len(flood_cited)}/{len(cited)}"
    )
