"""Seed helpers for the index lane's DB tests.

Plain-SQL inserts against an autocommit connection. Only the index lane's
substrate is exercised here, so seeded edges carry no ``edge_sources`` rows
(the provenance invariant is checked against isolated databases elsewhere).
"""

from __future__ import annotations

from uuid import UUID

import psycopg

from wikimind.index.store import format_vector


def seed_project(conn: psycopg.Connection, name: str) -> UUID:
    return conn.execute("INSERT INTO projects (name) VALUES (%s) RETURNING id", (name,)).fetchone()[
        0
    ]


def seed_node(
    conn: psycopg.Connection,
    project_id: UUID,
    slug: str,
    *,
    node_type: str = "concept",
    status: str = "active",
) -> UUID:
    return conn.execute(
        """
        INSERT INTO nodes (project_id, slug, node_type, status)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (project_id, slug, node_type, status),
    ).fetchone()[0]


def seed_node_version(
    conn: psycopg.Connection,
    project_id: UUID,
    node_id: UUID,
    rendered_text: str,
    *,
    version: int = 1,
) -> UUID:
    return conn.execute(
        """
        INSERT INTO node_versions (project_id, node_id, version, rendered_text)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (project_id, node_id, version, rendered_text),
    ).fetchone()[0]


def seed_edge(
    conn: psycopg.Connection,
    project_id: UUID,
    src_node: UUID,
    dst_node: UUID,
    *,
    edge_type: str = "relates_to",
) -> UUID:
    return conn.execute(
        """
        INSERT INTO edges (project_id, src_node, dst_node, edge_type)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (project_id, src_node, dst_node, edge_type),
    ).fetchone()[0]


def seed_embedding(
    conn: psycopg.Connection,
    project_id: UUID,
    node_version_id: UUID,
    model: str,
    vector: list[float],
) -> None:
    conn.execute(
        """
        INSERT INTO embeddings (project_id, node_version_id, model, vec)
        VALUES (%s, %s, %s, %s::vector)
        """,
        (project_id, node_version_id, model, format_vector(vector)),
    )


def basis_vector(index: int, *, dim: int = 384) -> list[float]:
    """Unit vector along one axis, for exact cosine-similarity expectations."""

    vector = [0.0] * dim
    vector[index] = 1.0
    return vector
