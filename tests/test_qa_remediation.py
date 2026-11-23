"""Lane 9 remediation tests: R1–R4 and R6.

The CLI-level integration test is the Ruling 23 template: it drives the real
Typer entry point against a template-spawned DB with scripted LLM fakes, and
pipes ``ask --json`` straight into ``integrity verify-answer`` with no shim.
No keys, no network.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from index_seed import (
    basis_vector,
    seed_edge,
    seed_embedding,
    seed_node,
    seed_node_version,
)
from typer.testing import CliRunner

from wikimind.cli import app as root_app
from wikimind.integrity.costs import MIRROR_ENV_VAR, MirroringCostSink, build_meter
from wikimind.llm import CostMeter, FakeLLM
from wikimind.prices import EMBED_MODEL
from wikimind.qa import cli as qa_cli
from wikimind.qa.pipeline import ClaimRecord, _gather_claims_page
from wikimind.qa.prompts import CALL_SITE_EMBED, AnswerPayload, claim_ref
from wikimind.qa.verify import verify_citations

runner = CliRunner(env={"COLUMNS": "200"})


class _FixedEmbedder:
    model = EMBED_MODEL

    def __init__(self, meter: CostMeter, vector: list[float] | None = None) -> None:
        self.meter = meter
        self.vector = vector or basis_vector(0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.meter is not None:
            self.meter.record(
                call_site=CALL_SITE_EMBED, model=EMBED_MODEL, in_tokens=len(texts), out_tokens=0
            )
        return [list(self.vector) for _ in texts]


def _seed_ask_case(conn, project_name: str):
    """One project, one claim, one concept seed; returns (project, claim_id, slug)."""

    project_id = conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id", (project_name,)
    ).fetchone()[0]
    sha = uuid4().hex
    doc_id = conn.execute(
        "INSERT INTO documents (project_id, sha256, source_uri, parser, prompt_version, "
        "model, schema_version) VALUES (%s, %s, %s, 'text', 'compile-v0', 'fake-llm', 'v1') "
        "RETURNING id",
        (project_id, sha, "file:///papers/attn-l9-fixture.pdf"),
    ).fetchone()[0]
    citation = seed_node(conn, project_id, f"source:{sha}", node_type="citation")
    cversion = seed_node_version(conn, project_id, citation, "The Transformer uses attention.")
    seed_embedding(conn, project_id, cversion, EMBED_MODEL, basis_vector(100))
    concept = seed_node(conn, project_id, "attn-l9-concept", node_type="concept")
    nversion = seed_node_version(conn, project_id, concept, "Transformer attention architecture")
    seed_embedding(conn, project_id, nversion, EMBED_MODEL, basis_vector(0))
    edge = seed_edge(conn, project_id, concept, citation)

    channel_id = f"qa-l9-{uuid4().hex}"
    conn.execute(
        "INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text) "
        "VALUES (%s, %s, %s, 'title', 0, %s)",
        (channel_id, project_id, doc_id, "The Transformer uses attention."),
    )
    claim_id = conn.execute(
        "INSERT INTO claims (project_id, citation_node_id, channel_id, document_id, "
        "anchor_quote, anchor_span, text) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, %s) "
        "RETURNING id",
        (
            project_id,
            citation,
            channel_id,
            doc_id,
            "The Transformer uses attention.",
            "The Transformer uses attention.",
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
        (project_id, edge, claim_id),
    )
    return project_id, claim_id, "attn-l9-fixture"


def _install_fakes(monkeypatch, scripted: dict) -> None:
    """Replace the CLI's real model constructors with scripted fakes."""

    def make_drafter(*, meter=None, **kwargs):
        return FakeLLM([scripted], meter=meter)

    def make_embedder(*, meter=None, **kwargs):
        return _FixedEmbedder(meter)

    monkeypatch.setattr(qa_cli, "GeminiClient", make_drafter)
    monkeypatch.setattr(qa_cli, "SentenceTransformerEmbedder", make_embedder)


def test_ask_json_through_real_cli_matches_v4_contract(db_conn, test_dsn, monkeypatch, tmp_path):
    """R1/R2/Ruling 23: real Typer entry point, template DB, scripted fakes."""

    _project, claim_id, slug = _seed_ask_case(db_conn, "qa-l9-cli")
    db_conn.commit()
    ref = claim_ref(claim_id)
    scripted = {
        "answer": f"The Transformer uses attention [{ref}].",
        "sufficient": True,
        "citations": [{"claim_id": ref, "document_slug": slug}],
    }
    _install_fakes(monkeypatch, scripted)
    monkeypatch.delenv(MIRROR_ENV_VAR, raising=False)

    result = runner.invoke(
        root_app,
        [
            "ask",
            "What architecture?",
            "--project",
            "qa-l9-cli",
            "--dsn",
            test_dsn,
            "--costs-path",
            str(tmp_path / "costs.jsonl"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert set(payload) == {"answer", "sufficient", "citations"}
    assert payload["sufficient"] is True
    assert len(payload["citations"]) == 1
    assert set(payload["citations"][0]) == {"claim_id", "document_slug"}
    assert payload["citations"][0]["claim_id"] == str(claim_id)
    assert payload["citations"][0]["document_slug"] == slug
    # Ruling 22: prose keeps the short ref, only the array is canonical.
    assert ref in payload["answer"]
    assert str(claim_id) not in payload["answer"]


def test_ask_json_pipes_straight_into_verify_answer(db_conn, test_dsn, monkeypatch, tmp_path):
    """R2 cross-lane: zero shim between ``ask --json`` and ``verify-answer``."""

    _seed_ask_case(db_conn, "qa-l9-cross")
    db_conn.commit()
    claim_id = db_conn.execute(
        "SELECT id FROM claims WHERE project_id=(SELECT id FROM projects WHERE name='qa-l9-cross')"
    ).fetchone()[0]
    ref = claim_ref(claim_id)
    scripted = {
        "answer": f"The Transformer uses attention [{ref}].",
        "sufficient": True,
        "citations": [{"claim_id": ref, "document_slug": "attn-l9-fixture"}],
    }
    _install_fakes(monkeypatch, scripted)
    monkeypatch.delenv(MIRROR_ENV_VAR, raising=False)

    asked = runner.invoke(
        root_app,
        [
            "ask",
            "What architecture?",
            "--project",
            "qa-l9-cross",
            "--dsn",
            test_dsn,
            "--costs-path",
            str(tmp_path / "costs.jsonl"),
            "--json",
        ],
    )
    assert asked.exit_code == 0, asked.output
    answer_file = tmp_path / "answer.json"
    answer_file.write_text(asked.output.strip(), encoding="utf-8")

    verified = runner.invoke(
        root_app,
        [
            "integrity",
            "verify-answer",
            str(answer_file),
            "--project",
            "qa-l9-cross",
            "--dsn",
            test_dsn,
            "--json",
        ],
    )
    assert verified.exit_code == 0, verified.output
    report = json.loads(verified.output)
    assert report["ok"] is True
    assert report["counts"]["citations.total"] == 1
    assert report["counts"]["citations.known"] == 1
    assert report["counts"]["citations.supported"] == 1
    assert report["counts"].get("citations.unsupported", 0) == 0


def test_ask_uses_shared_mirror_sink_factory(db_conn, test_dsn, monkeypatch, tmp_path):
    """R3: ask honours WIKIMIND_COST_DB_MIRROR via the compile sink factory."""

    _seed_ask_case(db_conn, "qa-l9-mirror")
    db_conn.commit()
    ref = claim_ref(
        db_conn.execute(
            "SELECT id FROM claims WHERE project_id=(SELECT id FROM projects WHERE name='qa-l9-mirror')"
        ).fetchone()[0]
    )
    scripted = {
        "answer": f"Attention [{ref}].",
        "sufficient": True,
        "citations": [{"claim_id": ref, "document_slug": "attn-l9-fixture"}],
    }
    _install_fakes(monkeypatch, scripted)
    monkeypatch.setenv(MIRROR_ENV_VAR, "1")

    real_build = build_meter
    captured: dict[str, object] = {}

    def spy(path, *, dsn=None, environ=None):
        meter = real_build(path, dsn=dsn, environ=environ)
        captured["sink"] = meter.sink
        captured["dsn"] = dsn
        return meter

    monkeypatch.setattr(qa_cli, "build_meter", spy)
    result = runner.invoke(
        root_app,
        [
            "ask",
            "question?",
            "--project",
            "qa-l9-mirror",
            "--dsn",
            test_dsn,
            "--costs-path",
            str(tmp_path / "costs.jsonl"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert isinstance(captured["sink"], MirroringCostSink)
    assert captured["dsn"] == test_dsn


def test_fabricated_ref_removed_from_prose_and_citations() -> None:
    """R4/Ruling 24: unverified bracket goes; the sentence stays intact."""

    real = "c-aaaa11112222"
    provided = [
        ClaimRecord(
            ref=real,
            claim_id=uuid4(),
            text="real claim",
            document_slug="attention-paper",
        )
    ]
    # verify_citations works on PromptClaim; build one from the record.
    from wikimind.qa.prompts import PromptClaim

    prompt_claims = [
        PromptClaim(ref=c.ref, text=c.text, document_slug=c.document_slug) for c in provided
    ]
    payload = AnswerPayload.model_validate(
        {
            "answer": f"Real fact [{real}] and invented fact [c-deadbeef].",
            "sufficient": True,
            "citations": [
                {"claim_id": real, "document_slug": "attention-paper"},
                {"claim_id": "c-deadbeef", "document_slug": "made-up-source"},
            ],
        }
    )
    result = verify_citations(payload, prompt_claims)
    assert len(result.citations) == 1
    assert result.citations[0].claim_id == real
    assert result.unverified_removed == 1
    assert "[c-deadbeef]" not in result.answer
    assert f"Real fact [{real}] and invented fact." in result.answer


def test_claim_ranking_prefers_high_similarity_node_at_equal_depth(db_conn) -> None:
    """R6: high-score claim survives the cap; low-score one is cut first."""

    conn = db_conn
    project_id = conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id", (f"qa-l9-rank-{uuid4().hex[:6]}",)
    ).fetchone()[0]
    sha = uuid4().hex
    doc_id = conn.execute(
        "INSERT INTO documents (project_id, sha256, source_uri, parser, prompt_version, "
        "model, schema_version) VALUES (%s, %s, %s, 'text', 'compile-v0', 'fake-llm', 'v1') "
        "RETURNING id",
        (project_id, sha, "file:///ranking.txt"),
    ).fetchone()[0]

    seed = seed_node(conn, project_id, "qa-l9-seed", node_type="concept")
    high = seed_node(conn, project_id, "qa-l9-high", node_type="concept")
    low = seed_node(conn, project_id, "qa-l9-low", node_type="concept")
    high_citation = seed_node(conn, project_id, "source:high", node_type="citation")
    low_citation = seed_node(conn, project_id, "source:low", node_type="citation")
    high_edge = seed_edge(conn, project_id, high, high_citation)
    low_edge = seed_edge(conn, project_id, low, low_citation)

    def claim(text: str, citation: UUID) -> UUID:
        channel = f"ch-{uuid4().hex}"
        conn.execute(
            "INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text) "
            "VALUES (%s, %s, %s, 'title', 0, %s)",
            (channel, project_id, doc_id, text),
        )
        return conn.execute(
            "INSERT INTO claims (project_id, citation_node_id, channel_id, document_id, "
            "anchor_quote, anchor_span, text) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, %s) "
            "RETURNING id",
            (project_id, citation, channel, doc_id, text, text),
        ).fetchone()[0]

    relevant = claim("RELEVANT high-similarity claim", high_citation)
    irrelevant = claim("IRRELEVANT low-similarity claim", low_citation)
    conn.execute(
        "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
        (project_id, high_edge, relevant),
    )
    conn.execute(
        "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
        (project_id, low_edge, irrelevant),
    )

    depths = {seed: 0, high: 1, low: 1, high_citation: 2, low_citation: 2}
    scores = {seed: 1.0, high: 0.95, low: 0.10, high_citation: 0.0, low_citation: 0.0}

    full, _ = _gather_claims_page(conn, project_id, depths, cap=10, scores=scores)
    assert [c.claim_id for c in full][:2] == [relevant, irrelevant]

    capped, truncated = _gather_claims_page(conn, project_id, depths, cap=1, scores=scores)
    assert [c.claim_id for c in capped] == [relevant]
    assert truncated is True

    # With no scores the historical depth/id ordering still holds (regression).
    legacy, _ = _gather_claims_page(conn, project_id, depths, cap=10)
    assert {c.claim_id for c in legacy} == {relevant, irrelevant}
