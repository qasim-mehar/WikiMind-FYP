from __future__ import annotations

import uuid

import psycopg
import pytest

from wikimind.db.checks import EXPECTED_TABLES

VECTOR_384 = "[" + ",".join(["0"] * 384) + "]"


def _project(conn: psycopg.Connection, name: str = "project") -> object:
    row = conn.execute("INSERT INTO projects (name) VALUES (%s) RETURNING id", (name,)).fetchone()
    assert row is not None
    return row[0]


def _document(conn: psycopg.Connection, project_id: object) -> object:
    row = conn.execute(
        """
        INSERT INTO documents
            (project_id, sha256, source_uri, parser, prompt_version, model, schema_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            project_id,
            uuid.uuid4().hex,
            "file:///paper.pdf",
            "pdf",
            "v1",
            "model-a",
            "schema-v1",
        ),
    ).fetchone()
    assert row is not None
    return row[0]


def _node(conn: psycopg.Connection, project_id: object, slug: str, node_type: str) -> object:
    row = conn.execute(
        """
        INSERT INTO nodes (project_id, slug, node_type)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (project_id, slug, node_type),
    ).fetchone()
    assert row is not None
    return row[0]


def _version(conn: psycopg.Connection, project_id: object, node_id: object) -> object:
    row = conn.execute(
        """
        INSERT INTO node_versions (project_id, node_id, version, rendered_text)
        VALUES (%s, %s, 1, %s)
        RETURNING id
        """,
        (project_id, node_id, "Hello world. A rendered page."),
    ).fetchone()
    assert row is not None
    version_id = row[0]
    conn.execute(
        "UPDATE nodes SET current_version_id = %s WHERE id = %s",
        (version_id, node_id),
    )
    return version_id


def test_expected_tables_have_project_id_not_null(db_conn: psycopg.Connection) -> None:
    rows = db_conn.execute(
        """
        SELECT c.relname, a.attnotnull
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attribute a
          ON a.attrelid = c.oid
         AND a.attname = 'project_id'
         AND a.attnum > 0
         AND NOT a.attisdropped
        WHERE n.nspname = current_schema()
          AND c.relkind = 'r'
          AND c.relname = ANY(%s)
        """,
        (list(EXPECTED_TABLES),),
    ).fetchall()
    state = {str(row[0]): row[1] for row in rows}
    assert set(state) == set(EXPECTED_TABLES)
    assert all(state[table] is True for table in EXPECTED_TABLES)

    for table in ("projects", "schema_migrations"):
        exists = db_conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)
        ).fetchone()
        assert exists == (True,)


def test_document_memoization_key_is_unique(db_conn: psycopg.Connection) -> None:
    project_id = _project(db_conn, "memo")
    values = (
        project_id,
        "a" * 64,
        "file:///paper.pdf",
        "pdf",
        "v1",
        "model-a",
        "schema-v1",
    )
    db_conn.execute(
        """
        INSERT INTO documents
            (project_id, sha256, source_uri, parser, prompt_version, model, schema_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        values,
    )
    with pytest.raises(psycopg.errors.UniqueViolation), db_conn.transaction():
        db_conn.execute(
            """
                INSERT INTO documents
                    (project_id, sha256, source_uri, parser, prompt_version, model, schema_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
            values,
        )


def test_channels_are_unique_per_document(db_conn: psycopg.Connection) -> None:
    project_id = _project(db_conn, "dup-channels")
    doc_a = _document(db_conn, project_id)
    doc_b = _document(db_conn, project_id)
    shared_id = uuid.uuid4().hex

    for document_id in (doc_a, doc_b):
        db_conn.execute(
            """
            INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text)
            VALUES (%s, %s, %s, 'paragraph', 0, 'Identical title text.')
            """,
            (shared_id, project_id, document_id),
        )
    assert db_conn.execute(
        "SELECT count(*) FROM channels WHERE id = %s", (shared_id,)
    ).fetchone() == (2,)

    with pytest.raises(psycopg.errors.UniqueViolation), db_conn.transaction():
        db_conn.execute(
            """
            INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text)
            VALUES (%s, %s, %s, 'paragraph', 1, 'Identical title text.')
            """,
            (shared_id, project_id, doc_a),
        )


def test_claim_references_channel_per_document(db_conn: psycopg.Connection) -> None:
    project_id = _project(db_conn, "claim-channel-fk")
    doc_a = _document(db_conn, project_id)
    doc_b = _document(db_conn, project_id)
    channel_id = db_conn.execute(
        """
        INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text)
        VALUES (%s, %s, %s, 'paragraph', 0, 'Text only in document A.')
        RETURNING id
        """,
        (uuid.uuid4().hex, project_id, doc_a),
    ).fetchone()
    assert channel_id is not None

    with pytest.raises(psycopg.errors.ForeignKeyViolation), db_conn.transaction():
        db_conn.execute(
            """
            INSERT INTO claims
                (project_id, citation_node_id, channel_id, document_id,
                 anchor_quote, anchor_span, text)
            VALUES (%s, %s, %s, %s, 'x', '{}'::jsonb, 'claim')
            """,
            (
                project_id,
                _node(db_conn, project_id, "citation/x", "citation"),
                channel_id[0],
                doc_b,
            ),
        )


def test_claim_ledger_edges_and_provenance_round_trip(db_conn: psycopg.Connection) -> None:
    project_id = _project(db_conn, "graph")
    document_id = _document(db_conn, project_id)
    channel_text = "Attention is all you need."
    channel = db_conn.execute(
        """
        INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text)
        VALUES (%s, %s, %s, 'paragraph', 0, %s)
        RETURNING id
        """,
        (uuid.uuid4().hex, project_id, document_id, channel_text),
    ).fetchone()
    assert channel is not None

    citation_id = _node(db_conn, project_id, "citation/attention", "citation")
    entity_id = _node(db_conn, project_id, "entity/transformer", "entity")
    version_id = _version(db_conn, project_id, entity_id)

    claim_id = db_conn.execute(
        """
        INSERT INTO claims
            (project_id, citation_node_id, channel_id, document_id,
             anchor_quote, anchor_span, text)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING id
        """,
        (
            project_id,
            citation_id,
            channel[0],
            document_id,
            channel_text,
            '{"start": 0, "end": 26}',
            "The paper introduces the Transformer.",
        ),
    ).fetchone()
    assert claim_id is not None

    db_conn.execute(
        """
        INSERT INTO ledger_blocks
            (project_id, node_id, claim_id, position, block_type)
        VALUES (%s, %s, %s, 0, 'claim')
        """,
        (project_id, entity_id, claim_id[0]),
    )
    db_conn.execute(
        """
        INSERT INTO ledger_blocks (project_id, node_id, claim_id, position, block_type)
        VALUES (%s, %s, NULL, 1, 'derived')
        """,
        (project_id, entity_id),
    )

    edge_id = db_conn.execute(
        """
        INSERT INTO edges (project_id, src_node, dst_node, edge_type)
        VALUES (%s, %s, %s, 'cites')
        RETURNING id
        """,
        (project_id, entity_id, citation_id),
    ).fetchone()
    assert edge_id is not None
    db_conn.execute(
        "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
        (project_id, edge_id[0], claim_id[0]),
    )
    db_conn.execute(
        "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s)",
        (project_id, entity_id, document_id),
    )
    db_conn.execute(
        "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s)",
        (project_id, citation_id, document_id),
    )

    assert db_conn.execute(
        "SELECT count(*) FROM ledger_blocks WHERE node_id = %s", (entity_id,)
    ).fetchone() == (2,)
    assert db_conn.execute(
        "SELECT fts @@ plainto_tsquery('english', 'hello') FROM node_versions WHERE id = %s",
        (version_id,),
    ).fetchone() == (True,)
    assert db_conn.execute(
        "SELECT count(*) FROM edge_sources WHERE edge_id = %s", (edge_id[0],)
    ).fetchone() == (1,)

    with pytest.raises(psycopg.errors.UniqueViolation), db_conn.transaction():
        db_conn.execute(
            """
                INSERT INTO edges (project_id, src_node, dst_node, edge_type)
                VALUES (%s, %s, %s, 'cites')
                """,
            (project_id, entity_id, citation_id),
        )


def test_embedding_is_384_dimensional(db_conn: psycopg.Connection) -> None:
    project_id = _project(db_conn, "embed")
    node_id = _node(db_conn, project_id, "concept/attention", "concept")
    version_id = _version(db_conn, project_id, node_id)

    db_conn.execute(
        """
        INSERT INTO embeddings (project_id, node_version_id, model, vec)
        VALUES (%s, %s, 'bge-small', %s::vector)
        """,
        (project_id, version_id, VECTOR_384),
    )
    assert db_conn.execute(
        "SELECT vector_dims(vec) FROM embeddings WHERE node_version_id = %s",
        (version_id,),
    ).fetchone() == (384,)

    with pytest.raises(psycopg.Error), db_conn.transaction():
        db_conn.execute(
            """
                INSERT INTO embeddings (project_id, node_version_id, model, vec)
                VALUES (%s, %s, 'bad-model', '[1,2,3]'::vector)
                """,
            (project_id, version_id),
        )


def test_node_redirects_and_slug_identity(db_conn: psycopg.Connection) -> None:
    project_id = _project(db_conn, "redirects")
    old_node = _node(db_conn, project_id, "entity/old-name", "entity")
    new_node = _node(db_conn, project_id, "entity/new-name", "entity")

    db_conn.execute(
        "INSERT INTO node_redirects (project_id, from_slug, node_id) VALUES (%s, %s, %s)",
        (project_id, "entity/old-name", new_node),
    )
    assert db_conn.execute(
        "SELECT node_id FROM node_redirects WHERE project_id = %s AND from_slug = %s",
        (project_id, "entity/old-name"),
    ).fetchone() == (new_node,)

    with pytest.raises(psycopg.errors.UniqueViolation), db_conn.transaction():
        db_conn.execute(
            "INSERT INTO nodes (project_id, slug, node_type) VALUES (%s, %s, 'entity')",
            (project_id, "entity/new-name"),
        )

    assert old_node != new_node
