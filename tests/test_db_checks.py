from __future__ import annotations

import uuid

import psycopg

from wikimind.db.checks import check_database
from wikimind.db.migrations import run_migrations


def _migrated(dsn: str) -> psycopg.Connection:
    run_migrations(dsn)
    return psycopg.connect(dsn, autocommit=True)


def test_enum_domain_violation_is_detected(empty_db_dsn: str) -> None:
    conn = _migrated(empty_db_dsn)
    try:
        conn.execute("ALTER TABLE nodes DROP CONSTRAINT nodes_node_type_check")
        conn.execute("INSERT INTO projects (name) VALUES ('enum-bad') RETURNING id")
        project_id = conn.execute("SELECT id FROM projects").fetchone()[0]
        conn.execute(
            "INSERT INTO nodes (project_id, slug, node_type) VALUES (%s, 'bad', 'bogus')",
            (project_id,),
        )
    finally:
        conn.close()

    report = check_database(empty_db_dsn)
    assert report.exit_code == 2
    assert any(finding.code == "enum.invalid_value" for finding in report.findings)


def test_orphan_claim_is_detected(empty_db_dsn: str) -> None:
    conn = _migrated(empty_db_dsn)
    try:
        conn.execute("ALTER TABLE claims DROP CONSTRAINT claims_citation_node_id_fkey")
        project_id = conn.execute(
            "INSERT INTO projects (name) VALUES ('orphan') RETURNING id"
        ).fetchone()[0]
        document_id = conn.execute(
            """
            INSERT INTO documents
                (project_id, sha256, source_uri, parser, prompt_version, model, schema_version)
            VALUES (%s, 'orphan-sha', 'file:///x', 'text', 'v1', 'm', 's')
            RETURNING id
            """,
            (project_id,),
        ).fetchone()[0]
        channel_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text)
            VALUES (%s, %s, %s, 'paragraph', 0, 'raw')
            """,
            (channel_id, project_id, document_id),
        )
        conn.execute(
            """
            INSERT INTO claims
                (project_id, citation_node_id, channel_id, anchor_quote, anchor_span, text)
            VALUES (%s, %s, %s, 'raw', '{}'::jsonb, 'claim')
            """,
            (project_id, uuid.uuid4(), channel_id),
        )
    finally:
        conn.close()

    report = check_database(empty_db_dsn)
    assert report.exit_code == 2
    assert any(finding.code == "orphan.rows" for finding in report.findings)


def test_edge_without_provenance_is_detected(empty_db_dsn: str) -> None:
    conn = _migrated(empty_db_dsn)
    try:
        project_id = conn.execute(
            "INSERT INTO projects (name) VALUES ('edge') RETURNING id"
        ).fetchone()[0]
        src = conn.execute(
            "INSERT INTO nodes (project_id, slug, node_type) "
            "VALUES (%s, 'entity/a', 'entity') RETURNING id",
            (project_id,),
        ).fetchone()[0]
        dst = conn.execute(
            "INSERT INTO nodes (project_id, slug, node_type) "
            "VALUES (%s, 'entity/b', 'entity') RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO edges (project_id, src_node, dst_node, edge_type) "
            "VALUES (%s, %s, %s, 'relates_to')",
            (project_id, src, dst),
        )
    finally:
        conn.close()

    report = check_database(empty_db_dsn)
    assert report.exit_code == 2
    assert any(finding.code == "edge.provenance_missing" for finding in report.findings)


def test_nullable_project_id_is_detected(empty_db_dsn: str) -> None:
    conn = _migrated(empty_db_dsn)
    try:
        conn.execute("ALTER TABLE cost_events ALTER COLUMN project_id DROP NOT NULL")
    finally:
        conn.close()

    report = check_database(empty_db_dsn)
    assert report.exit_code == 2
    assert any(finding.code == "schema.project_id_nullable" for finding in report.findings)


def test_missing_table_is_detected(empty_db_dsn: str) -> None:
    conn = _migrated(empty_db_dsn)
    try:
        conn.execute("DROP TABLE embeddings")
    finally:
        conn.close()

    report = check_database(empty_db_dsn)
    assert report.exit_code == 2
    assert any(finding.code == "schema.missing_table" for finding in report.findings)
