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


def test_two_compile_versions_yield_one_row(test_dsn: str) -> None:
    """G3: the join uses the full compile_units PK, not just (doc, channel)."""

    conn = connect_db(test_dsn)
    try:
        project, document, channel, _claim, _edge = _seed_contradiction(conn)
        seed_compile_unit(conn, project, document, channel, compile_version="test-v2")
        conn.execute(
            "UPDATE compile_units SET compiled_at = now() + interval '1 hour' "
            "WHERE project_id = %s AND compile_version = %s",
            (str(project), "test-v2"),
        )
        report = list_contradictions(conn, project)
        assert report.ok
        assert report.counts["edges.contradicts"] == 1
        assert len(report.rows) == 1
        assert report.rows[0]["unit_compile_version"] == "test-v2"
        assert report.counts["units.recorded"] == 1
    finally:
        conn.close()


def test_same_channel_under_two_documents_counts_two_units(test_dsn: str) -> None:
    """G12: units.recorded keys on the full PK, not just channel+version."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        channel_id = "shared-channel-id"
        for index in range(2):
            document = seed_document(conn, project)
            seed_channel(conn, project, document, channel_id=channel_id)
            citation = seed_node(conn, project, f"source:shared-{index}", node_type="citation")
            seed_node(conn, project, f"alpha-{index}")
            claim = seed_claim(
                conn,
                project,
                citation,
                channel_id,
                document,
                text=f"Claim {index} contradicts another claim.",
            )
            seed_edge(
                conn,
                project,
                citation,
                citation,
                edge_type="contradicts",
                claims=(claim,),
            )
            seed_compile_unit(conn, project, document, channel_id)
        report = list_contradictions(conn, project)
        assert report.ok
        assert report.counts["edges.contradicts"] == 2
        assert report.counts["units.recorded"] == 2
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
