"""Minimal row seeds for integrity tests (no LLM calls, no network)."""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect_db(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


def seed_project(conn: psycopg.Connection, name: str | None = None) -> UUID:
    row = conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id",
        (name or f"integrity-{uuid4().hex[:10]}",),
    ).fetchone()
    return UUID(str(row["id"]))


def seed_document(
    conn: psycopg.Connection,
    project_id: UUID,
    *,
    sha256: str | None = None,
    source_uri: str | None = None,
) -> UUID:
    digest = sha256 or uuid4().hex + uuid4().hex
    row = conn.execute(
        "INSERT INTO documents (project_id, sha256, source_uri, parser, prompt_version, "
        "model, schema_version) VALUES (%s, %s, %s, 'text', 'parse-v1', 'fake', 'v1') "
        "RETURNING id",
        (str(project_id), digest, source_uri or f"memo://{digest[:12]}.txt"),
    ).fetchone()
    return UUID(str(row["id"]))


def seed_channel(
    conn: psycopg.Connection,
    project_id: UUID,
    document_id: UUID,
    *,
    channel_id: str = "seed-channel",
    text: str = "Alpha joined Beta Labs in 2017.",
) -> str:
    conn.execute(
        "INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text) "
        "VALUES (%s, %s, %s, 'section', 0, %s) ON CONFLICT (id, document_id) DO NOTHING",
        (channel_id, str(project_id), str(document_id), text),
    )
    return channel_id


def seed_node(
    conn: psycopg.Connection,
    project_id: UUID,
    slug: str,
    *,
    node_type: str = "entity",
    versioned: bool = True,
) -> UUID:
    row = conn.execute(
        "INSERT INTO nodes (project_id, slug, node_type) VALUES (%s, %s, %s) RETURNING id",
        (str(project_id), slug, node_type),
    ).fetchone()
    node = UUID(str(row["id"]))
    if versioned:
        version = conn.execute(
            "INSERT INTO node_versions (project_id, node_id, version, rendered_text) "
            "VALUES (%s, %s, 1, %s) RETURNING id",
            (str(project_id), str(node), f"{slug} [entity]"),
        ).fetchone()
        conn.execute(
            "UPDATE nodes SET current_version_id = %s WHERE id = %s",
            (str(version["id"]), str(node)),
        )
    if node_type != "memory":
        document = seed_document(conn, project_id)
        conn.execute(
            "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s)",
            (str(project_id), str(node), str(document)),
        )
    return node


def seed_claim(
    conn: psycopg.Connection,
    project_id: UUID,
    citation_node: UUID,
    channel_id: str,
    document_id: UUID,
    *,
    text: str = "Alpha joined Beta Labs in 2017.",
    claim_id: UUID | None = None,
) -> UUID:
    identity = claim_id or uuid4()
    conn.execute(
        "INSERT INTO claims (id, project_id, citation_node_id, channel_id, document_id, "
        "anchor_quote, anchor_span, text) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            str(identity),
            str(project_id),
            str(citation_node),
            channel_id,
            str(document_id),
            text,
            Jsonb({"basis": "raw", "start": 0, "end": len(text)}),
            text,
        ),
    )
    # Mirror real compile behaviour: the citation node is sourced from the
    # claim's own document (the canonical `db check` provenance rule).
    conn.execute(
        "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (str(project_id), str(citation_node), str(document_id)),
    )
    return identity


def seed_edge(
    conn: psycopg.Connection,
    project_id: UUID,
    src: UUID,
    dst: UUID,
    *,
    edge_type: str = "relates_to",
    claims: tuple[UUID, ...] = (),
) -> UUID:
    row = conn.execute(
        "INSERT INTO edges (project_id, src_node, dst_node, edge_type) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (str(project_id), str(src), str(dst), edge_type),
    ).fetchone()
    edge = UUID(str(row["id"]))
    for claim in claims:
        conn.execute(
            "INSERT INTO edge_sources (project_id, edge_id, claim_id) VALUES (%s, %s, %s)",
            (str(project_id), str(edge), str(claim)),
        )
    return edge


def seed_compile_unit(
    conn: psycopg.Connection,
    project_id: UUID,
    document_id: UUID,
    channel_id: str,
    *,
    run_id: UUID | None = None,
    compile_version: str = "test-v1",
) -> None:
    conn.execute(
        "INSERT INTO compile_units (project_id, document_id, channel_id, compile_version, run_id) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (
            str(project_id),
            str(document_id),
            channel_id,
            compile_version,
            None if run_id is None else str(run_id),
        ),
    )


def seed_run(conn: psycopg.Connection, project_id: UUID) -> UUID:
    row = conn.execute(
        "INSERT INTO compile_runs (project_id) VALUES (%s) RETURNING id", (str(project_id),)
    ).fetchone()
    return UUID(str(row["id"]))
