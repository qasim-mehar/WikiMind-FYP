"""V5: read-only contradiction registry."""

from __future__ import annotations

from integrity_seed import (
    connect_db,
    seed_channel,
    seed_claim,
    seed_compile_unit,
    seed_document,
    seed_edge,
    seed_node,
    seed_project,
)

from wikimind.integrity.contradictions import list_contradictions


def _seed_contradiction(conn, *, with_provenance: bool = True):
    project = seed_project(conn)
    document = seed_document(conn, project)
    channel = seed_channel(conn, project, document)
    citation = seed_node(conn, project, f"source:{document}", node_type="citation")
    seed_node(conn, project, "alpha")
    seed_node(conn, project, "beta")
    claim = seed_claim(conn, project, citation, channel, document, text="Alpha contradicts Beta.")
    seed_compile_unit(conn, project, document, channel)
    edge = seed_edge(
        conn,
        project,
        citation,
        citation,
        edge_type="contradicts",
        claims=(claim,) if with_provenance else (),
    )
    return project, document, channel, claim, edge


def test_contradiction_report_involves_claim_node_document_and_unit(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project, _document, channel, claim, edge = _seed_contradiction(conn)
        report = list_contradictions(conn, project)
        assert report.ok, report.findings
        assert report.exit_code == 0
        assert report.counts["edges.contradicts"] == 1
        assert report.counts["claims.involved"] == 1
        assert report.counts["documents.involved"] == 1
        assert report.counts["units.recorded"] == 1
        row = report.rows[0]
        assert row["edge_id"] == str(edge)
        assert row["claim_id"] == str(claim)
        assert row["document_sha256"] is not None
        assert row["unit_channel_id"] == channel
        assert row["src_node"].startswith("source:")
        assert report.to_dict()["rows"][0]["claim_id"] == str(claim)
    finally:
        conn.close()


def test_contradiction_without_provenance_is_a_finding(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project, *_ = _seed_contradiction(conn, with_provenance=False)
        report = list_contradictions(conn, project)
        assert not report.ok
        assert report.counts["edges.contradicts_without_sources"] == 1
        assert report.rows == []
    finally:
        conn.close()


def test_project_isolation(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project, *_ = _seed_contradiction(conn)
        other = seed_project(conn)
        assert list_contradictions(conn, other).counts["edges.contradicts"] == 0
        assert list_contradictions(conn, project).counts["edges.contradicts"] == 1
    finally:
        conn.close()
