"""Incremental embedding of node versions (schema.md ``embeddings``).

The canonical text of an embedding is ``node_versions.rendered_text``.
Version rows are immutable (schema.md: "Immutable after insert by
convention; no update path is provided"), so the version identity acts as a
content address: a version is already indexed exactly when an ``embeddings``
row exists for ``(node_version_id, model)``. A run therefore embeds only
versions that are new since the last run (or whose embedding row was
explicitly removed), and re-runs do nothing.  Only CURRENT node versions
are indexed: a version is a candidate exactly when it is the target of its
node's ``current_version_id``; superseded versions are never embedded.

Embeddings are produced through the L2 embedder protocol at call site
``index.embed`` so every call is metered into the cost ledger; the local
bge-small model carries a shadow price of $0 but is still recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg

from ..db.locks import with_writer_lock
from ..llm.protocols import Embedder
from ..prices import EMBED_DIM
from .store import insert_embeddings, stored_version_ids

INDEX_CALL_SITE = "index.embed"
DEFAULT_BATCH_SIZE = 64


@dataclass(frozen=True, slots=True)
class ProjectRef:
    """A resolved project: canonical id and display name."""

    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class IndexResult:
    """Counts for one index run over one project."""

    project_id: UUID
    model: str
    total: int
    embedded: int
    skipped: int


def resolve_project(conn: psycopg.Connection, ref: str) -> ProjectRef:
    """Resolve a project by UUID or exact display name.

    Names are not unique in the schema, so an ambiguous name is an error that
    lists the matching ids; pass one of them as a UUID to disambiguate.
    """

    try:
        as_uuid = UUID(ref)
    except ValueError:
        as_uuid = None
    if as_uuid is not None:
        row = conn.execute("SELECT id, name FROM projects WHERE id = %s", (as_uuid,)).fetchone()
        if row is not None:
            return ProjectRef(id=row[0], name=row[1])
        raise KeyError(f"no project with id {as_uuid}")

    rows = conn.execute(
        "SELECT id, name FROM projects WHERE name = %s ORDER BY id", (ref,)
    ).fetchall()
    if not rows:
        raise KeyError(f"no project named {ref!r}")
    if len(rows) > 1:
        ids = ", ".join(str(row[0]) for row in rows)
        raise ValueError(f"project name {ref!r} is ambiguous; pass one of: {ids}")
    return ProjectRef(id=rows[0][0], name=rows[0][1])


def embedder_model_label(embedder: Embedder) -> str:
    """Return the model identity an embedder carries, or raise.

    Both L2 embedders expose one (``FakeEmbedder.model`` and
    ``SentenceTransformerEmbedder.model_name``); the label is stored in
    ``embeddings.model`` so it cannot drift from the vectors actually
    written. A custom embedder without an identity needs an explicit
    ``model`` override.
    """

    label = getattr(embedder, "model", None) or getattr(embedder, "model_name", None)
    if not label:
        raise ValueError("embedder exposes no model identity; pass model= to index_project")
    return str(label)


def _candidate_versions(conn: psycopg.Connection, project_id: UUID) -> list[tuple[UUID, str]]:
    """Canonical texts of one project's CURRENT node versions, in id order.

    Only the version a node's ``current_version_id`` points at is indexed;
    superseded versions stay unembedded (their old embedding rows, if any,
    are simply never surfaced by retrieval, which applies the same filter).
    """

    rows = conn.execute(
        """
        SELECT nv.id, nv.rendered_text
        FROM node_versions nv
        JOIN nodes n ON n.id = nv.node_id
        WHERE nv.project_id = %s
          AND n.current_version_id = nv.id
        ORDER BY nv.id
        """,
        (project_id,),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def index_project(
    conn: psycopg.Connection,
    project_id: UUID,
    embedder: Embedder,
    *,
    model: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> IndexResult:
    """Embed every not-yet-indexed node version of one project.

    Reads candidates, embeds the pending ones in deterministic order (one
    metered embedder call per batch), then writes inside the project writer
    lock with ``ON CONFLICT DO NOTHING``, so repeated or concurrent runs are
    idempotent. ``conn`` must be idle (e.g. autocommit); the write phase
    commits on exit of the writer-lock transaction.

    ``model`` labels the stored rows and must match the vectors actually
    produced; by default it is derived from the embedder itself (see
    :func:`embedder_model_label`).
    """

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    model_label = model or embedder_model_label(embedder)

    candidates = _candidate_versions(conn, project_id)
    already = stored_version_ids(conn, project_id, model_label)
    pending = [(version_id, text) for version_id, text in candidates if version_id not in already]

    vectors: list[tuple[UUID, list[float]]] = []
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        embedded = embedder.embed([text for _, text in batch])
        if len(embedded) != len(batch):
            raise RuntimeError(
                f"embedder returned {len(embedded)} vectors for a batch of {len(batch)}"
            )
        for vector in embedded:
            if len(vector) != EMBED_DIM:
                raise ValueError(f"embedder returned dim {len(vector)}, expected {EMBED_DIM}")
        vectors.extend(zip((version_id for version_id, _ in batch), embedded))

    with with_writer_lock(project_id, conn):
        embedded_count = insert_embeddings(conn, project_id, model_label, vectors)

    return IndexResult(
        project_id=project_id,
        model=model_label,
        total=len(candidates),
        embedded=embedded_count,
        skipped=len(candidates) - embedded_count,
    )
