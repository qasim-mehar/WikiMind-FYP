"""V5: read-only registry of recorded ``contradicts`` dispositions.

Contradictions are recorded, never resolved (project stance).  This command
reports the involved claims, nodes and documents plus the compile unit that
recorded each disposition; it never mutates a row.
"""

from __future__ import annotations

from uuid import UUID

import psycopg

from .model import IntegrityReport

CONTRADICTIONS_SQL = """
    SELECT e.id AS edge_id,
           src.slug AS src_slug,
           dst.slug AS dst_slug,
           c.id AS claim_id,
           d.id AS document_id,
           d.sha256 AS document_sha256,
           d.source_uri AS source_uri,
           u.channel_id AS channel_id,
           u.compile_version AS compile_version,
           u.compiled_at AS compiled_at,
           u.run_id AS run_id,
           (SELECT string_agg(n.slug, ', ' ORDER BY b.position)
              FROM ledger_blocks b
              JOIN nodes n ON n.id = b.node_id AND n.project_id = b.project_id
             WHERE b.claim_id = c.id AND b.project_id = e.project_id) AS ledger_nodes
      FROM edges e
      JOIN nodes src ON src.id = e.src_node AND src.project_id = e.project_id
      JOIN nodes dst ON dst.id = e.dst_node AND dst.project_id = e.project_id
      JOIN edge_sources s ON s.edge_id = e.id AND s.project_id = e.project_id
      JOIN claims c ON c.id = s.claim_id AND c.project_id = e.project_id
      LEFT JOIN documents d ON d.id = c.document_id AND d.project_id = e.project_id
      -- compile_units PK is (document_id, channel_id, compile_version): join
      -- the full key by selecting the latest version for the channel, so a
      -- channel compiled under two prompts yields one row, not duplicates.
      LEFT JOIN compile_units u ON u.document_id = c.document_id
           AND u.channel_id = c.channel_id AND u.project_id = e.project_id
           AND u.compile_version = (
               SELECT u2.compile_version FROM compile_units u2
                WHERE u2.project_id = c.project_id
                  AND u2.document_id = c.document_id
                  AND u2.channel_id = c.channel_id
                ORDER BY u2.compiled_at DESC, u2.compile_version DESC
                LIMIT 1
           )
     WHERE e.project_id = %s AND e.edge_type = 'contradicts'
     ORDER BY e.id, c.id
"""


def list_contradictions(conn: psycopg.Connection, project_id: UUID | str) -> IntegrityReport:
    """List every recorded contradiction disposition for a project."""

    report = IntegrityReport("contradictions")
    pid = str(project_id)
    missing = conn.execute(
        "SELECT count(*) AS n FROM edges e WHERE e.project_id = %s "
        "AND e.edge_type = 'contradicts' AND NOT EXISTS "
        "(SELECT 1 FROM edge_sources s WHERE s.edge_id = e.id AND s.project_id = e.project_id)",
        (pid,),
    ).fetchone()["n"]
    report.set_count("edges.contradicts_without_sources", int(missing))
    if missing:
        report.finding(f"{int(missing)} contradicts edge(s) have no provenance claim")

    rows = conn.execute(CONTRADICTIONS_SQL, (pid,)).fetchall()
    claims: set[str] = set()
    documents: set[str] = set()
    # G12: key on the full compile_units PK (plus project) so the same
    # channel id compiled under two documents counts as two units.
    units: set[tuple[str, str, str, str]] = set()
    for row in rows:
        claim_id = str(row["claim_id"])
        document = None if row["document_sha256"] is None else str(row["document_sha256"])
        claims.add(claim_id)
        if document:
            documents.add(document)
        if row["channel_id"]:
            units.add(
                (
                    pid,
                    str(row["document_id"]),
                    str(row["channel_id"]),
                    str(row["compile_version"]),
                )
            )
        record = {
            "edge_id": str(row["edge_id"]),
            "src_node": row["src_slug"],
            "dst_node": row["dst_slug"],
            "claim_id": claim_id,
            "document_sha256": document,
            "document_source_uri": None if row["source_uri"] is None else str(row["source_uri"]),
            "ledger_nodes": row["ledger_nodes"] or "",
            "unit_channel_id": None if row["channel_id"] is None else str(row["channel_id"]),
            "unit_compile_version": None
            if row["compile_version"] is None
            else str(row["compile_version"]),
            "unit_compiled_at": None if row["compiled_at"] is None else str(row["compiled_at"]),
            "unit_run_id": None if row["run_id"] is None else str(row["run_id"]),
        }
        report.rows.append(record)
    report.set_count("edges.contradicts", len(rows))
    report.set_count("claims.involved", len(claims))
    report.set_count("documents.involved", len(documents))
    report.set_count("units.recorded", len(units))
    return report
