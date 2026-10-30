"""Deterministic node rendering and node-version materialization.

Governance Ruling 14: compile must materialize node versions.  The rendered
text of a node is a pure function of stored rows — the node's slug and type
plus the texts of the claims connected to it — so the same rows always render
the same bytes, on the compile path and on backfill alike.

A claim is *connected* to a node when it is stored against that node:
either the claim cites the node (``claims.citation_node_id``) or one of its
provenance edges (``edge_sources``) is incident to the node.  Claim order is
by claim text (id as tie-break), so rendering is independent of insertion
order, of ``PYTHONHASHSEED``, and of per-run identifier randomness.

Version materialization is append-only: when the freshly rendered text
differs from the node's current version, a new version row (previous version
number + 1) is inserted and ``nodes.current_version_id`` advances; identical
text is a no-op.  Nothing is ever updated or deleted.

All mutating helpers here are lock-free: callers hold the canonical
per-project writer lock (``wikimind.db.with_writer_lock``) around them.
``materialize_version`` verifies the lock is actually held (via
``pg_locks``) and fails loudly when it is not, instead of relying on this
docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg

from ..db.locks import with_writer_lock, writer_lock_held


def _field(row: Any, name: str, index: int) -> Any:
    """Read one column from a row under either row factory.

    The compile pipeline runs on ``dict_row`` connections while tests and the
    index lane use plain tuples; render must work against both.
    """

    return row[name] if isinstance(row, dict) else row[index]


def _require_row(row: Any, what: str) -> Any:
    """Turn a row that the schema guarantees into a loud failure if absent."""

    if row is None:
        raise RuntimeError(f"expected row missing from database: {what}")
    return row


def render_node_text(
    slug: str,
    node_type: str,
    claims: list[tuple[UUID, str]],
) -> str:
    """Render a node's canonical text from its identity and connected claims.

    ``claims`` is a list of ``(claim_id, text)``.  It is sorted by claim text
    (id as tie-break) so the result depends only on the claim content, never
    on the caller's iteration order — and, since claim ids embed the random
    project id, never on per-run identifier randomness either.
    """

    lines = [f"{slug} [{node_type}]"]
    for _, text in sorted(claims, key=lambda claim: (claim[1], claim[0])):
        lines.append("")
        lines.append(f"- {text}")
    return "\n".join(lines)


def node_claim_texts(
    conn: psycopg.Connection, project_id: UUID, node_id: UUID
) -> list[tuple[UUID, str]]:
    """The (claim_id, text) pairs connected to one node, from stored rows."""

    rows = conn.execute(
        """
        SELECT c.id, c.text
        FROM claims c
        WHERE c.project_id = %s
          AND (
            c.citation_node_id = %s
            OR EXISTS (
                SELECT 1 FROM edge_sources es
                JOIN edges e ON e.id = es.edge_id
                WHERE es.claim_id = c.id
                  AND es.project_id = %s
                  AND e.project_id = %s
                  AND (e.src_node = %s OR e.dst_node = %s)
            )
          )
        ORDER BY c.id
        """,
        (project_id, node_id, project_id, project_id, node_id, node_id),
    ).fetchall()
    return [(_field(row, "id", 0), _field(row, "text", 1)) for row in rows]


def render_node(conn: psycopg.Connection, project_id: UUID, node_id: UUID) -> str:
    """Render a node from the rows currently stored for it."""

    row = conn.execute(
        "SELECT slug, node_type FROM nodes WHERE id = %s AND project_id = %s",
        (node_id, project_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"node {node_id} not found in project {project_id}")
    claims = node_claim_texts(conn, project_id, node_id)
    return render_node_text(_field(row, "slug", 0), _field(row, "node_type", 1), claims)


def materialize_version(
    conn: psycopg.Connection,
    project_id: UUID,
    node_id: UUID,
) -> str:
    """Append a node version when the rendered text changed; return the action.

    ``"created"`` — the node had no versions at all (version 1 written and
    ``nodes.current_version_id`` set, in the caller's transaction).
    ``"appended"`` — the rendered text differs from the current version;
    version n+1 is appended and ``current_version_id`` advances.
    ``"unchanged"`` — the current version already matches; nothing is written.

    The caller must hold the project writer lock; this is verified against
    ``pg_locks`` and a violation fails loudly rather than silently racing a
    concurrent writer.
    """

    if not writer_lock_held(conn, project_id):
        raise RuntimeError(
            f"materialize_version requires the project writer lock for {project_id}; "
            "wrap the call in wikimind.db.with_writer_lock"
        )

    rendered = render_node(conn, project_id, node_id)
    current = conn.execute(
        """
        SELECT nv.rendered_text
        FROM nodes n
        LEFT JOIN node_versions nv ON nv.id = n.current_version_id
        WHERE n.id = %s AND n.project_id = %s
        """,
        (node_id, project_id),
    ).fetchone()
    if current is None:
        raise ValueError(f"node {node_id} not found in project {project_id}")
    current_text = _field(current, "rendered_text", 0)

    if current_text == rendered:
        return "unchanged"

    next_version = _require_row(
        conn.execute(
            """
            SELECT coalesce(max(version), 0) + 1 AS next_version
            FROM node_versions
            WHERE node_id = %s
            """,
            (node_id,),
        ).fetchone(),
        f"next node_version number for node {node_id}",
    )

    version_id = _require_row(
        conn.execute(
            """
            INSERT INTO node_versions (project_id, node_id, version, rendered_text)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (project_id, node_id, _field(next_version, "next_version", 0), rendered),
        ).fetchone(),
        f"inserted node_version row for node {node_id}",
    )
    conn.execute(
        "UPDATE nodes SET current_version_id = %s WHERE id = %s AND project_id = %s",
        (_field(version_id, "id", 0), node_id, project_id),
    )
    return "created" if current_text is None else "appended"


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """Counts for one backfill run over one project."""

    nodes_total: int
    versioned: int
    already_versioned: int
    repaired: int = 0


def backfill_project(conn: psycopg.Connection, project_id: UUID) -> BackfillResult:
    """Version every node of the project that has no node_versions row.

    Backfill fidelity choice: one version per node, rendered from the node's
    full stored state.  Per-unit version history is not reconstructed —
    claim rows carry no commit ordering (ids are content hashes, not
    sequence numbers), so a faithful replay would mean re-deriving the commit
    order of units that compile-v0 never recorded per version, and the result
    could not be verified against anything.  A single current version is what
    the embedding path needs, and it is idempotent.

    Nodes that already have versions are left untouched: their history is
    authoritative and was written by the compile path.  The one exception is
    a node whose history exists but whose ``current_version_id`` is NULL (a
    pre-Ruling-14 artifact): backfill repairs it to point at its latest
    version without writing any new version row, and counts it in
    ``repaired``.  Runs under the project writer lock; a second run writes
    nothing.
    """

    with with_writer_lock(project_id, conn):
        total = _require_row(
            conn.execute(
                "SELECT count(*) AS n FROM nodes WHERE project_id = %s", (project_id,)
            ).fetchone(),
            f"node count for project {project_id}",
        )
        rows = conn.execute(
            """
            SELECT n.id
            FROM nodes n
            WHERE n.project_id = %s
              AND NOT EXISTS (SELECT 1 FROM node_versions v WHERE v.node_id = n.id)
            ORDER BY n.id
            """,
            (project_id,),
        ).fetchall()
        versioned = 0
        for row in rows:
            if materialize_version(conn, project_id, _field(row, "id", 0)) == "created":
                versioned += 1

        repaired = 0
        null_rows = conn.execute(
            """
            SELECT n.id
            FROM nodes n
            WHERE n.project_id = %s
              AND n.current_version_id IS NULL
              AND EXISTS (SELECT 1 FROM node_versions v WHERE v.node_id = n.id)
            ORDER BY n.id
            """,
            (project_id,),
        ).fetchall()
        for row in null_rows:
            node_id = _field(row, "id", 0)
            conn.execute(
                """
                UPDATE nodes
                SET current_version_id = (
                    SELECT v.id FROM node_versions v
                    WHERE v.node_id = %s
                    ORDER BY v.version DESC, v.id DESC
                    LIMIT 1
                )
                WHERE id = %s AND project_id = %s
                """,
                (node_id, node_id, project_id),
            )
            repaired += 1

    nodes_total = _field(total, "n", 0)
    return BackfillResult(
        nodes_total=nodes_total,
        versioned=versioned,
        already_versioned=nodes_total - versioned - repaired,
        repaired=repaired,
    )
