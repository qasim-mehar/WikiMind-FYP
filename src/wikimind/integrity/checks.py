"""V1: provenance checker over a compiled project.

Every query is project-scoped.  Counts are exposed per check, and findings
include up to ``MAX_EXAMPLES`` example IDs so a human can act on them without
dumping a whole corpus into the console.
"""

from __future__ import annotations

from uuid import UUID

import psycopg

from .model import IntegrityReport

MAX_EXAMPLES = 20

SQL_PROJECT = "%s"


def _scalar(conn: psycopg.Connection, sql: str, params: tuple[object, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return 0 if row is None else int(row["n"])


def _examples(
    conn: psycopg.Connection,
    sql: str,
    params: tuple[object, ...],
    column: str,
) -> list[str]:
    rows = conn.execute(f"{sql} LIMIT {MAX_EXAMPLES}", params).fetchall()
    return [str(row[column]) for row in rows]


def _add_examples(report: IntegrityReport, label: str, values: list[str]) -> None:
    for value in values:
        report.finding(f"orphan {label} {value}")
    if len(values) == MAX_EXAMPLES:
        report.finding(f"orphan {label}: more than {MAX_EXAMPLES} (truncated)")


def check_provenance(conn: psycopg.Connection, project_id: UUID | str) -> IntegrityReport:
    """Walk one project's graph and report provenance/version findings."""

    report = IntegrityReport("provenance")
    pid = str(project_id)

    # --- claims: a real document via the (channel_id, document_id) provenance.
    report.set_count(
        "claims.total",
        _scalar(conn, "SELECT count(*) AS n FROM claims WHERE project_id = %s", (pid,)),
    )
    bad_doc = "SELECT c.id AS id FROM claims c WHERE c.project_id = %s AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = c.document_id AND d.project_id = c.project_id)"
    bad_channel = "SELECT c.id AS id FROM claims c WHERE c.project_id = %s AND NOT EXISTS (SELECT 1 FROM channels ch WHERE ch.id = c.channel_id AND ch.document_id = c.document_id AND ch.project_id = c.project_id)"
    bad_node = "SELECT c.id AS id FROM claims c WHERE c.project_id = %s AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = c.citation_node_id AND n.project_id = c.project_id)"
    claim_doc_n = _scalar(
        conn,
        "SELECT count(*) AS n FROM claims c WHERE c.project_id = %s AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.id = c.document_id AND d.project_id = c.project_id)",
        (pid,),
    )
    claim_channel_n = _scalar(
        conn,
        "SELECT count(*) AS n FROM claims c WHERE c.project_id = %s AND NOT EXISTS (SELECT 1 FROM channels ch WHERE ch.id = c.channel_id AND ch.document_id = c.document_id AND ch.project_id = c.project_id)",
        (pid,),
    )
    claim_node_n = _scalar(
        conn,
        "SELECT count(*) AS n FROM claims c WHERE c.project_id = %s AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = c.citation_node_id AND n.project_id = c.project_id)",
        (pid,),
    )
    report.set_count("claims.missing_document", claim_doc_n)
    report.set_count("claims.missing_channel", claim_channel_n)
    report.set_count("claims.missing_citation_node", claim_node_n)
    if claim_doc_n or claim_channel_n:
        report.finding(
            f"{claim_doc_n} claim(s) without a document, {claim_channel_n} without a channel"
        )
        _add_examples(
            report, "claim", _examples(conn, f"{bad_doc} UNION {bad_channel}", (pid, pid), "id")
        )
    if claim_node_n:
        report.finding(f"{claim_node_n} claim(s) without a citation node")
        _add_examples(report, "claim", _examples(conn, bad_node, (pid,), "id"))

    # --- edges: every edge must carry edge_sources provenance.
    report.set_count(
        "edges.total",
        _scalar(conn, "SELECT count(*) AS n FROM edges WHERE project_id = %s", (pid,)),
    )
    edge_n = _scalar(
        conn,
        "SELECT count(*) AS n FROM edges e WHERE e.project_id = %s AND NOT EXISTS (SELECT 1 FROM edge_sources s WHERE s.edge_id = e.id AND s.project_id = e.project_id)",
        (pid,),
    )
    source_n = _scalar(
        conn,
        "SELECT count(*) AS n FROM edge_sources s WHERE s.project_id = %s AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.id = s.claim_id AND c.project_id = s.project_id)",
        (pid,),
    )
    report.set_count("edges.without_sources", edge_n)
    report.set_count("edge_sources.orphan", source_n)
    if edge_n:
        report.finding(f"{edge_n} edge(s) without edge_sources provenance")
        _add_examples(
            report,
            "edge",
            _examples(
                conn,
                "SELECT e.id AS id FROM edges e WHERE e.project_id = %s AND NOT EXISTS (SELECT 1 FROM edge_sources s WHERE s.edge_id = e.id)",
                (pid,),
                "id",
            ),
        )
    if source_n:
        report.finding(f"{source_n} edge_sources row(s) point at a missing claim")

    # --- nodes: node_sources, >=1 version, and a current_version_id that is
    # one of the node's own versions.  Memory nodes may have empty sources
    # (PROJECT.md 6.4.2); they are counted separately, not flagged.
    report.set_count(
        "nodes.total",
        _scalar(conn, "SELECT count(*) AS n FROM nodes WHERE project_id = %s", (pid,)),
    )
    node_source_n = _scalar(
        conn,
        "SELECT count(*) AS n FROM nodes n WHERE n.project_id = %s AND n.node_type <> 'memory' AND NOT EXISTS (SELECT 1 FROM node_sources s WHERE s.node_id = n.id AND s.project_id = n.project_id)",
        (pid,),
    )
    memory_no_sources = _scalar(
        conn,
        "SELECT count(*) AS n FROM nodes n WHERE n.project_id = %s AND n.node_type = 'memory' AND NOT EXISTS (SELECT 1 FROM node_sources s WHERE s.node_id = n.id AND s.project_id = n.project_id)",
        (pid,),
    )
    no_version_n = _scalar(
        conn,
        "SELECT count(*) AS n FROM nodes n WHERE n.project_id = %s AND NOT EXISTS (SELECT 1 FROM node_versions v WHERE v.node_id = n.id AND v.project_id = n.project_id)",
        (pid,),
    )
    bad_current_n = _scalar(
        conn,
        "SELECT count(*) AS n FROM nodes n WHERE n.project_id = %s AND (n.current_version_id IS NULL OR NOT EXISTS (SELECT 1 FROM node_versions v WHERE v.id = n.current_version_id AND v.node_id = n.id AND v.project_id = n.project_id))",
        (pid,),
    )
    report.set_count("nodes.without_sources", node_source_n)
    report.set_count("nodes.memory_without_sources", memory_no_sources)
    report.set_count("nodes.without_versions", no_version_n)
    report.set_count("nodes.bad_current_version", bad_current_n)
    if node_source_n:
        report.finding(f"{node_source_n} node(s) without node_sources provenance")
        _add_examples(
            report,
            "node",
            _examples(
                conn,
                "SELECT n.slug AS id FROM nodes n WHERE n.project_id = %s AND n.node_type <> 'memory' AND NOT EXISTS (SELECT 1 FROM node_sources s WHERE s.node_id = n.id)",
                (pid,),
                "id",
            ),
        )
    if no_version_n:
        report.finding(f"{no_version_n} node(s) without any node version")
        _add_examples(
            report,
            "node",
            _examples(
                conn,
                "SELECT n.slug AS id FROM nodes n WHERE n.project_id = %s AND NOT EXISTS (SELECT 1 FROM node_versions v WHERE v.node_id = n.id)",
                (pid,),
                "id",
            ),
        )
    if bad_current_n:
        report.finding(f"{bad_current_n} node(s) with a NULL or foreign current_version_id")
        _add_examples(
            report,
            "node",
            _examples(
                conn,
                "SELECT n.slug AS id FROM nodes n WHERE n.project_id = %s AND (n.current_version_id IS NULL OR NOT EXISTS (SELECT 1 FROM node_versions v WHERE v.id = n.current_version_id AND v.node_id = n.id))",
                (pid,),
                "id",
            ),
        )

    # --- compile units reconcile with channels.
    report.set_count(
        "compile_units.total",
        _scalar(conn, "SELECT count(*) AS n FROM compile_units WHERE project_id = %s", (pid,)),
    )
    report.set_count(
        "channels.total",
        _scalar(conn, "SELECT count(*) AS n FROM channels WHERE project_id = %s", (pid,)),
    )
    orphan_units = _scalar(
        conn,
        "SELECT count(*) AS n FROM compile_units u WHERE u.project_id = %s AND NOT EXISTS (SELECT 1 FROM channels ch WHERE ch.id = u.channel_id AND ch.document_id = u.document_id AND ch.project_id = u.project_id)",
        (pid,),
    )
    pending_channels = _scalar(
        conn,
        "SELECT count(*) AS n FROM channels ch WHERE ch.project_id = %s AND NOT EXISTS (SELECT 1 FROM compile_units u WHERE u.channel_id = ch.id AND u.document_id = ch.document_id)",
        (pid,),
    )
    report.set_count("compile_units.orphan", orphan_units)
    report.set_count("channels.without_compile_unit", pending_channels)
    if orphan_units:
        report.finding(f"{orphan_units} compile unit(s) do not match a channel")
        _add_examples(
            report,
            "compile unit",
            _examples(
                conn,
                "SELECT u.channel_id AS id FROM compile_units u WHERE u.project_id = %s AND NOT EXISTS (SELECT 1 FROM channels ch WHERE ch.id = u.channel_id AND ch.document_id = u.document_id)",
                (pid,),
                "id",
            ),
        )
    return report
