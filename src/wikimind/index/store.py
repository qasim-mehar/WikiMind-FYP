"""Embedding store access (schema.md ``embeddings``).

The store itself already exists in ``migrations/001_schema_v1.sql``: one row
per ``(node_version_id, model)`` with a ``vector(384)`` column and an HNSW
cosine index. This module is the write path other index modules share; lanes
should not hand-write SQL against ``embeddings``.

Vectors cross the wire as pgvector string literals built by
:func:`format_vector`, so no client-side pgvector dependency is needed.
"""

from __future__ import annotations

import math
from uuid import UUID

import psycopg

_INSERT_EMBEDDING_SQL = """
INSERT INTO embeddings (project_id, node_version_id, model, vec)
VALUES (%s, %s, %s, %s::vector)
ON CONFLICT (node_version_id, model) DO NOTHING
"""


def format_vector(vector: list[float]) -> str:
    """Render a float sequence as a pgvector string literal.

    Non-finite components are rejected here so a broken embedder fails with a
    clear Python error instead of a Postgres syntax error inside the write
    transaction.
    """

    values = [float(component) for component in vector]
    if not values:
        raise ValueError("embedding vectors must not be empty")
    for value in values:
        if not math.isfinite(value):
            raise ValueError("embedding vectors must be finite")
    return "[" + ", ".join(repr(value) for value in values) + "]"


def stored_version_ids(conn: psycopg.Connection, project_id: UUID, model: str) -> set[UUID]:
    """Return the node version ids that already carry an embedding for ``model``."""

    rows = conn.execute(
        "SELECT node_version_id FROM embeddings WHERE project_id = %s AND model = %s",
        (project_id, model),
    ).fetchall()
    return {row[0] for row in rows}


def insert_embeddings(
    conn: psycopg.Connection,
    project_id: UUID,
    model: str,
    rows: list[tuple[UUID, list[float]]],
) -> int:
    """Store ``(node_version_id, vec)`` rows; return how many were newly written.

    ``ON CONFLICT DO NOTHING`` keyed on the schema's
    ``UNIQUE (node_version_id, model)`` makes repeated or concurrent index
    runs idempotent: a row that already exists is left untouched, never
    duplicated.
    """

    stored = 0
    for node_version_id, vector in rows:
        result = conn.execute(
            _INSERT_EMBEDDING_SQL,
            (project_id, node_version_id, model, format_vector(vector)),
        )
        stored += max(result.rowcount, 0)
    return stored
