"""V4: answer JSON contract and citation verification."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from integrity_seed import (
    connect_db,
    seed_channel,
    seed_claim,
    seed_document,
    seed_edge,
    seed_node,
    seed_project,
)

from wikimind.integrity.answers import (
    AnswerDocument,
    Citation,
    document_identifiers,
    load_answer_document,
    parse_answer_document,
    verify_answer,
)

CONTRACT = {
    "answer": "Alpha joined Beta Labs in 2017.",
    "sufficient": True,
    "citations": [{"claim_id": str(uuid4()), "document_slug": "source:abc"}],
}


def test_parse_answer_contract() -> None:
    parsed = parse_answer_document(json.dumps(CONTRACT))
    assert parsed.answer == CONTRACT["answer"]
    assert parsed.sufficient is True
    assert len(parsed.citations) == 1
    assert parsed.citations[0].claim_id == CONTRACT["citations"][0]["claim_id"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"answer": 3},
        {"sufficient": "yes"},
        {"citations": {}},
        {"citations": [{"claim_id": "x"}]},
        {"citations": ["not-an-object"]},
    ],
)
def test_parse_rejects_contract_violations(mutation: dict) -> None:
    payload = {**CONTRACT, **mutation}
    with pytest.raises((ValueError, TypeError)):
        parse_answer_document(json.dumps(payload))
    with pytest.raises(ValueError):
        parse_answer_document(json.dumps({"answer": "x"}))


def test_load_answer_document(tmp_path) -> None:
    path = tmp_path / "answer.json"
    path.write_text(json.dumps(CONTRACT), encoding="utf-8")
    assert load_answer_document(path).sufficient is True


def test_document_identifiers_accept_stable_derivations() -> None:
    digest = "a" * 64
    ids = document_identifiers(
        sha256=digest,
        source_uri="memo://attention-1706.03762.txt",
        node_slug=f"source:{digest}",
        document_id=str(uuid4()),
    )
    for candidate in (
        digest,
        digest[:12],
        digest[:16],
        f"source:{digest}",
        "attention-1706-03762",  # Ruling 21: canonical slugified stem
        "memo://attention-1706.03762.txt",
    ):
        assert candidate in ids
    # N8: 8-hex prefixes are deliberately too loose.
    assert digest[:8] not in ids
    assert f"source:{digest[:8]}" not in ids
    assert "not-the-document" not in ids


def test_verify_answer_supported_and_unsupported(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        document = seed_document(conn, project, source_uri="memo://paper-one.txt")
        digest = conn.execute(
            "SELECT sha256 FROM documents WHERE id = %s", (str(document),)
        ).fetchone()["sha256"]
        channel = seed_channel(conn, project, document)
        citation_node = seed_node(conn, project, f"source:{digest}", node_type="citation")
        claim = seed_claim(conn, project, citation_node, channel, document)
        seed_edge(conn, project, citation_node, seed_node(conn, project, "alpha"), claims=(claim,))

        for slug in (f"source:{digest}", digest, digest[:16], "paper-one"):
            answer = AnswerDocument("x", True, (Citation(str(claim), slug),))
            report = verify_answer(conn, answer, project_id=project)
            assert report.ok, (slug, report.findings)
            assert report.counts["citations.supported"] == 1

        bad = AnswerDocument("x", True, (Citation(str(claim), "another-paper"),))
        report = verify_answer(conn, bad, project_id=project)
        assert not report.ok
        assert report.counts["citations.unsupported"] == 1
        assert any("unsupported citation" in finding for finding in report.findings)

        unknown = AnswerDocument("x", True, (Citation(str(uuid4()), "paper-one"),))
        report = verify_answer(conn, unknown, project_id=project)
        assert report.counts["citations.unknown_claim"] == 1
        assert report.exit_code == 1

        # Unscoped verification still resolves a globally unique claim id.
        assert verify_answer(conn, AnswerDocument("x", False, (Citation(str(claim), digest),))).ok
    finally:
        conn.close()
