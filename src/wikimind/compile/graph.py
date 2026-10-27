"""Database access for the compile lane.

All functions take an open ``psycopg.Connection`` using ``dict_row``.
Every query is project-scoped; every mutation runs through
``wikimind.db.with_writer_lock`` (the canonical single-writer lock).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Json

from ..db.locks import with_writer_lock
from .errors import AmbiguousProjectError, ProjectNotFoundError
from .ids import (
    citation_node_slug,
    claim_id,
    edge_id,
    node_id,
    normalize_claim_text,
    slugify_entity,
)
from .models import ProposedEntity, ProposedRelation
from .prompts import COMPILE_PROMPT_VERSION

# Channel kinds in document order; used only to iterate units in a stable,
# deterministic sequence (L1 ordinals count per kind, not across the document).
KIND_RANK = {
    "title": 0,
    "abstract": 1,
    "section": 2,
    "table": 3,
    "figure_caption": 4,
    "reference": 5,
}

# The documents memo key describes what produced the channels.  Channel
# production is the deterministic Lane 1 parse, not an LLM call.
DOCUMENT_PROMPT_VERSION_PREFIX = "parse-v"
DOCUMENT_MODEL = "deterministic"
DOCUMENT_SCHEMA_VERSION = "v1"

UUID_TEXT_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

# edges.edge_type CHECK domain (schema contract); predicates outside it
# degrade to relates_to.
EDGE_TYPE_DOMAIN = {
    "relates_to",
    "part_of",
    "supports",
    "contradicts",
    "supersedes",
    "cites",
    "authored_by",
    "uses",
    "measured_by",
}


@dataclass(frozen=True, slots=True)
class ProjectRef:
    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class DocumentRow:
    id: UUID
    sha256: str
    source_uri: str


@dataclass(frozen=True, slots=True)
class Unit:
    """One addressable channel unit of one document."""

    document_id: UUID
    document_sha256: str
    channel_id: str
    kind: str
    ordinal: int
    raw_text: str

    @property
    def unit_key(self) -> tuple[UUID, str]:
        return (self.document_id, self.channel_id)


@dataclass(frozen=True, slots=True)
class NeighborhoodClaim:
    """An existing claim shown to WRITE, with the entity slugs it touches."""

    id: UUID
    text: str
    entities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptedClaim:
    """A candidate that passed every gate and is ready to commit."""

    text: str
    support_quote: str
    entities: tuple[ProposedEntity, ...]
    relations: tuple[ProposedRelation, ...]
    disposition: str  # "new" | "supersedes" | "contradicts"
    target_claim_id: UUID | None


@dataclass(frozen=True, slots=True)
class CommitCounts:
    claims: int = 0
    nodes_created: int = 0
    edges_created: int = 0


def resolve_project(conn: psycopg.Connection, ref: str) -> ProjectRef:
    """Resolve a project by UUID or by unique name."""

    try:
        project_uuid = UUID(ref)
    except ValueError:
        rows = conn.execute(
            "SELECT id, name FROM projects WHERE name = %s ORDER BY id", (ref,)
        ).fetchall()
        if not rows:
            raise ProjectNotFoundError(f"no project named {ref!r}") from None
        if len(rows) > 1:
            ids = ", ".join(str(row["id"]) for row in rows)
            raise AmbiguousProjectError(
                f"project name {ref!r} is ambiguous; disambiguate by id: {ids}"
            ) from None
        row = rows[0]
        return ProjectRef(id=row["id"], name=row["name"])
    row = conn.execute("SELECT id, name FROM projects WHERE id = %s", (project_uuid,)).fetchone()
    if row is None:
        raise ProjectNotFoundError(f"no project with id {ref!r}")
    return ProjectRef(id=row["id"], name=row["name"])


def create_project(conn: psycopg.Connection, name: str) -> ProjectRef:
    row = conn.execute(
        "INSERT INTO projects (name) VALUES (%s) RETURNING id, name", (name,)
    ).fetchone()
    return ProjectRef(id=row["id"], name=row["name"])


def list_projects(conn: psycopg.Connection) -> list[ProjectRef]:
    rows = conn.execute("SELECT id, name FROM projects ORDER BY created_at, id").fetchall()
    return [ProjectRef(id=row["id"], name=row["name"]) for row in rows]


def document_memo_values(parsed: Any) -> tuple[str, str, str]:
    """Return the (prompt_version, model, schema_version) memo components."""

    return (
        f"{DOCUMENT_PROMPT_VERSION_PREFIX}{parsed.schema_version}",
        DOCUMENT_MODEL,
        DOCUMENT_SCHEMA_VERSION,
    )


def register_document(
    conn: psycopg.Connection,
    project_id: UUID,
    parsed: Any,
    source_uri: str,
) -> tuple[UUID, bool]:
    """Insert the documents row and its channels idempotently.

    Returns ``(document_id, created)``.  The memo key is the L1 memoization
    key from the schema contract; re-adding the same bytes with the same
    parser identities is a no-op.  Channel rows use the per-document primary
    key ``(id, document_id)`` from migration 002.
    """

    prompt_version, model, schema_version = document_memo_values(parsed)
    cursor = conn.execute(
        """
        INSERT INTO documents
            (project_id, sha256, source_uri, parser, prompt_version, model, schema_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (project_id, sha256, parser, prompt_version, model, schema_version)
        DO NOTHING
        """,
        (
            project_id,
            parsed.source_sha256,
            source_uri,
            parsed.parser,
            prompt_version,
            model,
            schema_version,
        ),
    )
    created = cursor.rowcount == 1
    row = conn.execute(
        """
        SELECT id FROM documents
        WHERE project_id = %s AND sha256 = %s AND parser = %s
          AND prompt_version = %s AND model = %s AND schema_version = %s
        """,
        (
            project_id,
            parsed.source_sha256,
            parsed.parser,
            prompt_version,
            model,
            schema_version,
        ),
    ).fetchone()
    document_id = row["id"]

    for channel in parsed.channels:
        conn.execute(
            """
            INSERT INTO channels (id, project_id, document_id, kind, ordinal, raw_text)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id, document_id) DO NOTHING
            """,
            (
                channel.channel_id,
                project_id,
                document_id,
                channel.kind.value,
                channel.ordinal,
                channel.text,
            ),
        )
    return document_id, created


def list_documents(conn: psycopg.Connection, project_id: UUID) -> list[DocumentRow]:
    rows = conn.execute(
        "SELECT id, sha256, source_uri FROM documents WHERE project_id = %s ORDER BY id",
        (project_id,),
    ).fetchall()
    return [
        DocumentRow(id=row["id"], sha256=row["sha256"], source_uri=row["source_uri"])
        for row in rows
    ]


def list_units(
    conn: psycopg.Connection, project_id: UUID, document_id: UUID | None = None
) -> list[Unit]:
    """All channel units of the project (or one document), in stable order."""

    query = sql.SQL(
        """
        SELECT d.id AS document_id, d.sha256 AS document_sha256,
               c.id AS channel_id, c.kind, c.ordinal, c.raw_text
        FROM channels c
        JOIN documents d ON d.id = c.document_id
        WHERE d.project_id = %s
        """
    )
    params: list[Any] = [project_id]
    if document_id is not None:
        query += sql.SQL(" AND d.id = %s")
        params.append(document_id)
    rows = conn.execute(query, params).fetchall()
    units = [
        Unit(
            document_id=row["document_id"],
            document_sha256=row["document_sha256"],
            channel_id=row["channel_id"],
            kind=row["kind"],
            ordinal=row["ordinal"],
            raw_text=row["raw_text"],
        )
        for row in rows
    ]
    units.sort(key=lambda unit: (unit.document_id, KIND_RANK.get(unit.kind, 99), unit.ordinal))
    return units


def compiled_unit_keys(
    conn: psycopg.Connection, project_id: UUID, compile_version: str
) -> set[tuple[UUID, str]]:
    """Unit-level short-circuit state: (document_id, channel_id) already done."""

    rows = conn.execute(
        """
        SELECT document_id, channel_id FROM compile_units
        WHERE project_id = %s AND compile_version = %s
        """,
        (project_id, compile_version),
    ).fetchall()
    return {(row["document_id"], row["channel_id"]) for row in rows}


def known_claim_texts(conn: psycopg.Connection, project_id: UUID) -> set[str]:
    """Normalized texts of all existing claims — the dedup pre-filter set."""

    rows = conn.execute("SELECT text FROM claims WHERE project_id = %s", (project_id,)).fetchall()
    return {normalize_claim_text(row["text"]) for row in rows}


def fetch_neighborhood(
    conn: psycopg.Connection,
    project_id: UUID,
    entity_slugs: list[str],
    limit: int,
) -> list[NeighborhoodClaim]:
    """Existing claims that touch any of the given entity slugs (SQL only).

    A claim touches a slug when one of its provenance edges is incident to a
    node with that slug.  No pgvector here: semantic neighborhoods arrive
    with the index lane, after Gate B.
    """

    if not entity_slugs or limit <= 0:
        return []
    rows = conn.execute(
        """
        SELECT c.id, c.text, matched.entities
        FROM claims c
        JOIN (
            SELECT es.claim_id, array_agg(DISTINCT n.slug) AS entities
            FROM edge_sources es
            JOIN edges e ON e.id = es.edge_id
            JOIN nodes n ON n.id IN (e.src_node, e.dst_node)
            WHERE n.project_id = es.project_id AND n.slug = ANY(%s)
            GROUP BY es.claim_id
        ) matched ON matched.claim_id = c.id
        WHERE c.project_id = %s
        ORDER BY c.id
        LIMIT %s
        """,
        (entity_slugs, project_id, limit),
    ).fetchall()
    return [
        NeighborhoodClaim(
            id=row["id"],
            text=row["text"],
            entities=tuple(row["entities"] or ()),
        )
        for row in rows
    ]


def claim_entity_slugs(conn: psycopg.Connection, project_id: UUID, claim: UUID) -> set[str]:
    """Entity/concept slugs a claim touches via its provenance edges."""

    rows = conn.execute(
        """
        SELECT DISTINCT n.slug
        FROM edge_sources es
        JOIN edges e ON e.id = es.edge_id
        JOIN nodes n ON n.id IN (e.src_node, e.dst_node)
        WHERE es.claim_id = %s AND es.project_id = %s
          AND n.project_id = %s AND n.node_type IN ('entity', 'concept')
        """,
        (claim, project_id, project_id),
    ).fetchall()
    return {row["slug"] for row in rows}


def parse_target_claim_id(raw: str | None) -> UUID | None:
    """Normalize a WRITE ``target_claim_id`` ("claim:<uuid>" or bare uuid)."""

    if not raw:
        return None
    text = raw.strip().lower()
    prefix = "claim:"
    text = text.removeprefix(prefix)
    if len(text) != 36 or text[8] != "-" or text[13] != "-" or text[18] != "-" or text[23] != "-":
        return None
    try:
        return UUID(text)
    except ValueError:
        return None


def entity_slugs_for(entities: list[ProposedEntity] | tuple[ProposedEntity, ...]) -> list[str]:
    """Slugify proposed entities, preserving first-seen order, skipping blanks."""

    slugs: list[str] = []
    for entity in entities:
        slug = slugify_entity(entity.name)
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def locate_span(raw_text: str, quote: str) -> dict[str, Any]:
    """Locate ``quote`` inside ``raw_text`` for the claim's anchor_span."""

    index = raw_text.find(quote)
    if index >= 0:
        return {"start": index, "end": index + len(quote), "basis": "raw"}
    normalized_raw = normalize_claim_text(raw_text)
    normalized_quote = normalize_claim_text(quote)
    normalized_index = normalized_raw.find(normalized_quote)
    if normalized_index >= 0:
        return {
            "start": normalized_index,
            "end": normalized_index + len(normalized_quote),
            "basis": "normalized",
        }
    return {"start": None, "end": None, "basis": "not_found"}


def is_anchored(raw_text: str, quote: str) -> bool:
    """True when the (D4-unescaped) quote is a verbatim substring of the unit."""

    unescaped = quote.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    normalized_quote = normalize_claim_text(unescaped)
    if not normalized_quote:
        return False
    return normalized_quote in normalize_claim_text(raw_text)


def _get_or_create_node(
    conn: psycopg.Connection,
    project_id: UUID,
    slug: str,
    node_type: str,
    created_counter: list[int],
) -> UUID:
    row = conn.execute(
        "SELECT id FROM nodes WHERE project_id = %s AND slug = %s",
        (project_id, slug),
    ).fetchone()
    if row is not None:
        return row["id"]
    new_id = node_id(project_id=project_id, slug=slug)
    conn.execute(
        """
        INSERT INTO nodes (id, project_id, slug, node_type)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (project_id, slug) DO NOTHING
        """,
        (new_id, project_id, slug, node_type),
    )
    row = conn.execute(
        "SELECT id FROM nodes WHERE project_id = %s AND slug = %s",
        (project_id, slug),
    ).fetchone()
    if row["id"] == new_id:
        created_counter[0] += 1
    return row["id"]


def _insert_edge(
    conn: psycopg.Connection,
    project_id: UUID,
    src_node: UUID,
    dst_node: UUID,
    edge_type: str,
    claim: UUID,
    extra_sources: tuple[UUID, ...] = (),
    created_counter: list[int] | None = None,
) -> None:
    edge_uuid = edge_id(
        project_id=project_id, src_node=src_node, dst_node=dst_node, edge_type=edge_type
    )
    cursor = conn.execute(
        """
        INSERT INTO edges (id, project_id, src_node, dst_node, edge_type)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (project_id, src_node, dst_node, edge_type) DO NOTHING
        """,
        (edge_uuid, project_id, src_node, dst_node, edge_type),
    )
    if cursor.rowcount == 1 and created_counter is not None:
        created_counter[0] += 1
    for source_claim in (claim, *extra_sources):
        conn.execute(
            """
            INSERT INTO edge_sources (project_id, edge_id, claim_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (edge_id, claim_id) DO NOTHING
            """,
            (project_id, edge_uuid, source_claim),
        )


def _node_type_for_entity(entity_type: str) -> str:
    """Map a proposed entity type onto the nodes.node_type domain."""

    return "concept" if entity_type == "concept" else "entity"


def commit_unit(
    conn: psycopg.Connection,
    *,
    project_id: UUID,
    unit: Unit,
    accepted: list[AcceptedClaim],
    compile_version: str = COMPILE_PROMPT_VERSION,
    run_id: UUID | None = None,
) -> CommitCounts:
    """Commit one unit's accepted claims under the canonical writer lock.

    One transaction inserts: the document's citation node (per D2, one
    reference node per document), entity/concept nodes, mention and relation
    edges with claim-level provenance, supersedes/contradicts disposition
    edges, the claims themselves, and the compile_units marker that makes
    re-runs skip this unit.  An empty ``accepted`` list still writes the
    marker so an all-rejected unit is never reprocessed.
    """

    created_nodes: list[int] = [0]
    created_edges: list[int] = [0]
    with with_writer_lock(project_id, conn):
        citation_slug = citation_node_slug(unit.document_sha256)
        citation_node = _get_or_create_node(
            conn, project_id, citation_slug, "citation", created_nodes
        )
        conn.execute(
            """
            INSERT INTO node_sources (project_id, node_id, document_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (node_id, document_id) DO NOTHING
            """,
            (project_id, citation_node, unit.document_id),
        )

        for accepted_claim in accepted:
            claim_uuid = claim_id(
                project_id=project_id,
                document_id=unit.document_id,
                channel_id=unit.channel_id,
                claim_text=accepted_claim.text,
                compile_version=compile_version,
            )
            conn.execute(
                """
                INSERT INTO claims
                    (id, project_id, citation_node_id, channel_id, document_id,
                     anchor_quote, anchor_span, text, created_by_job)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    claim_uuid,
                    project_id,
                    citation_node,
                    unit.channel_id,
                    unit.document_id,
                    accepted_claim.support_quote,
                    Json(locate_span(unit.raw_text, accepted_claim.support_quote)),
                    accepted_claim.text,
                    None,  # created_by_job: V0 compile runs are not jobs rows
                ),
            )

            slugs = entity_slugs_for(accepted_claim.entities)
            slug_to_node: dict[str, UUID] = {}
            for slug in slugs:
                proposed_type = next(
                    (
                        entity.type
                        for entity in accepted_claim.entities
                        if slugify_entity(entity.name) == slug
                    ),
                    "other",
                )
                node_uuid = _get_or_create_node(
                    conn, project_id, slug, _node_type_for_entity(proposed_type), created_nodes
                )
                slug_to_node[slug] = node_uuid
                conn.execute(
                    """
                    INSERT INTO node_sources (project_id, node_id, document_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (node_id, document_id) DO NOTHING
                    """,
                    (project_id, node_uuid, unit.document_id),
                )
                # The citation node's mention edge carries claim-level
                # provenance for every entity the claim proposes.
                _insert_edge(
                    conn,
                    project_id,
                    citation_node,
                    node_uuid,
                    "relates_to",
                    claim_uuid,
                    created_counter=created_edges,
                )

            for relation in accepted_claim.relations:
                subject_slug = slugify_entity(relation.subject)
                object_slug = slugify_entity(relation.object)
                # Relations to literal values (quantities, dates) never become
                # graph edges: both ends must be entities of this claim.
                if subject_slug not in slug_to_node or object_slug not in slug_to_node:
                    continue
                predicate = relation.predicate.strip().lower()
                if predicate not in EDGE_TYPE_DOMAIN:
                    predicate = "relates_to"
                _insert_edge(
                    conn,
                    project_id,
                    slug_to_node[subject_slug],
                    slug_to_node[object_slug],
                    predicate,
                    claim_uuid,
                    created_counter=created_edges,
                )

            if accepted_claim.disposition in ("supersedes", "contradicts"):
                target_node = conn.execute(
                    "SELECT citation_node_id FROM claims WHERE id = %s AND project_id = %s",
                    (accepted_claim.target_claim_id, project_id),
                ).fetchone()
                if target_node is not None:
                    _insert_edge(
                        conn,
                        project_id,
                        citation_node,
                        target_node["citation_node_id"],
                        accepted_claim.disposition,
                        claim_uuid,
                        extra_sources=(accepted_claim.target_claim_id,),
                        created_counter=created_edges,
                    )

        conn.execute(
            """
            INSERT INTO compile_units (project_id, document_id, channel_id, compile_version, run_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (document_id, channel_id, compile_version) DO NOTHING
            """,
            (project_id, unit.document_id, unit.channel_id, compile_version, run_id),
        )

    return CommitCounts(
        claims=len(accepted),
        nodes_created=created_nodes[0],
        edges_created=created_edges[0],
    )
