"""V1 provenance checker tests against a migrated throwaway database."""

from __future__ import annotations

from uuid import uuid4

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

from wikimind.integrity.checks import check_provenance


def _well_formed(conn, *, with_provenance: bool = True):
    project = seed_project(conn)
    document = seed_document(conn, project)
    channel = seed_channel(conn, project, document, channel_id=f"ch-{uuid4().hex[:8]}")
    citation = seed_node(conn, project, f"source:{document}", node_type="citation")
    entity = seed_node(conn, project, "alpha")
    claim = seed_claim(conn, project, citation, channel, document)
    edge = seed_edge(conn, project, citation, entity, claims=(claim,))
    seed_compile_unit(conn, project, document, channel)
    return project, document, channel, citation, entity, claim, edge


def test_clean_project_has_no_findings(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project, *_ = _well_formed(conn)
        report = check_provenance(conn, project)
        assert report.ok, report.findings
        assert report.exit_code == 0
        assert report.counts["claims.total"] == 1
        assert report.counts["edges.total"] == 1
        assert report.counts["nodes.total"] == 2
        assert report.counts["compile_units.total"] == 1
        assert report.counts["channels.total"] == 1
        assert "provenance" in report.render()
        assert report.to_dict()["ok"] is True
    finally:
        conn.close()


def test_findings_are_counted_and_reported(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project, document, _channel, _citation, entity, _claim, edge = _well_formed(conn)
        conn.execute("DELETE FROM node_sources WHERE node_id = %s", (str(entity),))
        conn.execute("DELETE FROM node_versions WHERE node_id = %s", (str(entity),))
        conn.execute("UPDATE nodes SET current_version_id = NULL WHERE id = %s", (str(entity),))
        conn.execute("DELETE FROM edge_sources WHERE edge_id = %s", (str(edge),))
        conn.execute(
            "INSERT INTO compile_units (project_id, document_id, channel_id, compile_version) "
            "VALUES (%s, %s, %s, 'test-v1')",
            (str(project), str(document), "missing-channel"),
        )
        report = check_provenance(conn, project)
        assert not report.ok
        assert report.exit_code == 1
        assert report.counts["nodes.without_sources"] == 1
        assert report.counts["nodes.without_versions"] == 1
        assert report.counts["nodes.bad_current_version"] == 1
        assert report.counts["edges.without_sources"] == 1
        assert report.counts["compile_units.orphan"] == 1
        assert any("orphan edge" in finding for finding in report.findings)
        assert any("orphan node" in finding for finding in report.findings)
        assert any("compile unit" in finding for finding in report.findings)
        assert "claims.total" in report.to_dict()["counts"]
    finally:
        conn.close()


def test_checks_are_project_scoped(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project, *_ = _well_formed(conn)
        other, *_ = _well_formed(conn)
        conn.execute("DELETE FROM node_sources WHERE project_id = %s", (str(other),))
        assert check_provenance(conn, project).ok
        assert not check_provenance(conn, other).ok
    finally:
        conn.close()


def test_memory_nodes_may_have_empty_sources(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        memory = seed_node(conn, project, "session-summary", node_type="memory", versioned=True)
        conn.execute("DELETE FROM node_sources WHERE node_id = %s", (str(memory),))
        report = check_provenance(conn, project)
        assert report.ok
        assert report.counts["nodes.memory_without_sources"] == 1
        assert report.counts["nodes.without_sources"] == 0
    finally:
        conn.close()
