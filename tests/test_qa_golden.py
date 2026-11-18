"""Golden QA acceptance: derived questions answered on seeded template DBs.

The question set is a tracked artifact (``tests/golden/qa_questions.json``)
derived from the locked parse fixtures. Each case seeds a throwaway project
with the claim texts recorded in that file, retrieves through the real QA
pipeline, and checks that the expected source documents surface and that a
scripted, fully-cited answer round-trips. No keys, no network, no live corpus.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from index_seed import basis_vector, seed_edge, seed_embedding, seed_node, seed_node_version

from wikimind.llm import CostMeter, FakeLLM, InMemorySink
from wikimind.prices import EMBED_MODEL
from wikimind.qa.pipeline import answer_question, document_slug
from wikimind.qa.prompts import CALL_SITE_EMBED, claim_ref

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "qa_questions.json"


def _load() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _project(conn) -> UUID:
    return conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id", (f"qa-golden-{uuid4().hex[:8]}",)
    ).fetchone()[0]


def _claim(conn, project_id, document_id, citation_node_id, text: str) -> UUID:
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


def _seed_case(conn, entry: dict):
    project_id = _project(conn)
    slug_by_fixture: dict[str, str] = {}
    claims: list[tuple[UUID, str, str]] = []
    for index, seed in enumerate(entry["seed_claims"]):
        sha = uuid4().hex
        document_id = conn.execute(
            "INSERT INTO documents (project_id, sha256, source_uri, parser, prompt_version, "
            "model, schema_version) VALUES (%s, %s, %s, 'text', 'compile-v0', 'fake-llm', 'v1') "
            "RETURNING id",
            (project_id, sha, f"file:///{seed['document']}"),
        ).fetchone()[0]
        doc_slug = document_slug(f"file:///{seed['document']}")
        slug_by_fixture[seed["document"]] = doc_slug

        citation = seed_node(conn, project_id, f"source:{sha}", node_type="citation")
        cversion = seed_node_version(conn, project_id, citation, seed["text"])
        seed_embedding(conn, project_id, cversion, EMBED_MODEL, basis_vector(40 + index))

        concept = seed_node(conn, project_id, f"qa-{entry['id']}-{index}", node_type="concept")
        nversion = seed_node_version(conn, project_id, concept, seed["text"])
        seed_embedding(conn, project_id, nversion, EMBED_MODEL, basis_vector(0))

        edge = seed_edge(conn, project_id, concept, citation)
        claim_id = _claim(conn, project_id, document_id, citation, seed["text"])
        conn.execute(
            "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
            (project_id, edge, claim_id),
        )
        claims.append((claim_id, doc_slug, seed["text"]))

    # A disconnected distractor: a real node with a claim, but never a seed and
    # never reachable through the seeded graph.
    sha = uuid4().hex
    document_id = conn.execute(
        "INSERT INTO documents (project_id, sha256, source_uri, parser, prompt_version, "
        "model, schema_version) VALUES (%s, %s, %s, 'text', 'compile-v0', 'fake-llm', 'v1') "
        "RETURNING id",
        (project_id, sha, "file:///distractor.txt"),
    ).fetchone()[0]
    distractor = seed_node(conn, project_id, "qa-distractor", node_type="concept")
    dversion = seed_node_version(conn, project_id, distractor, "orbital mechanics of Mars")
    seed_embedding(conn, project_id, dversion, EMBED_MODEL, basis_vector(20))
    dcitation = seed_node(conn, project_id, f"source:{sha}", node_type="citation")
    seed_node_version(conn, project_id, dcitation, "orbital mechanics of Mars")
    distractor_claim = _claim(conn, project_id, document_id, dcitation, "orbital mechanics of Mars")
    distractor_slug = document_slug("file:///distractor.txt")
    return project_id, slug_by_fixture, claims, distractor_claim, distractor_slug


class FixedEmbedder:
    model = EMBED_MODEL

    def __init__(self, meter: CostMeter) -> None:
        self.meter = meter

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.meter.record(call_site=CALL_SITE_EMBED, model=EMBED_MODEL, in_tokens=1)
        return [basis_vector(0) for _ in texts]


def test_golden_file_structure() -> None:
    data = _load()
    assert data["schema_version"] == "qa-golden-v1"
    derived_names = {Path(path).name for path in data["derived_from"]}
    questions = data["questions"]
    assert len(questions) == 15
    ids = [entry["id"] for entry in questions]
    assert len(set(ids)) == 15
    texts = [entry["question"] for entry in questions]
    assert len(set(texts)) == 15
    for entry in questions:
        assert entry["expected_answer_elements"]
        assert entry["expected_source_documents"]
        assert entry["seed_claims"]
        assert {c["document"] for c in entry["seed_claims"]} <= derived_names
        for source in entry["expected_source_documents"]:
            assert source in {c["document"] for c in entry["seed_claims"]}
    assert len(data["derived_from"]) == 4
    assert "contradiction-note.txt" in {c["document"] for c in questions[-1]["seed_claims"]}


def test_every_golden_question_answers_with_expected_sources(db_conn) -> None:
    for entry in _load()["questions"]:
        project_id, slug_by_fixture, claims, distractor_claim, distractor_slug = _seed_case(
            db_conn, entry
        )
        expected_slugs = {slug_by_fixture[doc] for doc in entry["expected_source_documents"]}
        refs = [claim_ref(claim_id) for claim_id, _, _ in claims]
        scripted = {
            "answer": " ".join([*entry["expected_answer_elements"], *(f"[{ref}]" for ref in refs)]),
            "sufficient": True,
            "citations": [
                {"claim_id": ref, "document_slug": slug} for ref, (_, slug, _) in zip(refs, claims)
            ],
        }
        meter = CostMeter(InMemorySink())
        fake = FakeLLM([scripted], meter=meter)

        result = answer_question(
            db_conn,
            project_id,
            entry["question"],
            embedder=FixedEmbedder(meter),
            drafter=fake,
            meter=meter,
            ann_k=len(expected_slugs),
        )

        # F6: genuine retrieval checks, not assertions on our own scripted text.
        retrieved_slugs = {claim.document_slug for claim in result.claims}
        assert expected_slugs <= retrieved_slugs, entry["id"]
        assert distractor_slug not in retrieved_slugs, entry["id"]
        assert all(claim.claim_id != distractor_claim for claim in result.claims), entry["id"]
        assert all(claim.text in fake.calls[0].user for claim in result.claims), entry["id"]
        assert result.sufficient is True, entry["id"]
        assert result.unverified_removed == 0, entry["id"]
        assert len(fake.calls) == 1, entry["id"]
