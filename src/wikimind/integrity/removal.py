"""D5/D7 removal workflow: tombstone, retract, and safe duplicate removal.

Removal is deletion treated as re-derivation, not a chat command:

* ``tombstone`` (default): the document's citation node moves to ``archived``.
  Its history (versions, claims, ledger, provenance) is kept; the QA lane
  excludes archived nodes from answers.
* ``retracted``: the citation node moves to ``retracted``.  Lint surfaces every
  claim whose *entire* support set is retracted documents.
* ``duplicate``: the reference node and its incident edges are deleted, but
  only when no other active page cites the document and no ledger block is
  anchored at the node or its claims.  That last guard is mandatory: the
  hash-chained ``ledger_blocks`` table is append-only, and an FK cascade would
  leave a non-contiguous chain (``verify-ledger`` would correctly fail).

Every path takes the canonical per-project writer lock, writes a durable job,
appends audit events, and runs a leak scan after the mutation.  There are no
LLM calls and no cost-ledger writes.  No migration is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg

from wikimind.compile.ids import citation_node_slug
from wikimind.db.locks import with_writer_lock
from wikimind.render.versions import render_node

from .answers import document_identifiers
from .events import REMOVAL_COMPLETED, REMOVAL_REFUSED, emit_event
from .jobs import JOB_KIND_REMOVAL, create_job, set_job_state
from .ledger import append_ledger_batch
from .model import IntegrityReport

MODE_TOMBSTONE = "tombstone"
MODE_RETRACTED = "retracted"
MODE_DUPLICATE = "duplicate"
REMOVAL_MODES = (MODE_TOMBSTONE, MODE_RETRACTED, MODE_DUPLICATE)
STATUS_FOR_MODE: dict[str, str] = {MODE_TOMBSTONE: "archived", MODE_RETRACTED: "retracted"}
REMOVED_STATUSES = ("archived", "retracted")
MAX_EXAMPLES = 20


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """One document and its citation node, resolved under a project scope."""

    project_id: str
    document_id: str
    sha256: str
    source_uri: str
    node_id: str
    node_slug: str
    status: str


def resolve_document(conn: psycopg.Connection, project_id: UUID | str, target: str) -> DocumentRef:
    """Resolve a target (sha256/prefix, source_uri, filename stem, citation slug)."""

    pid = str(project_id)
    needle = str(target).strip().lower()
    if not needle:
        raise ValueError("empty document target")
    rows = conn.execute(
        "SELECT d.id AS document_id, d.sha256, d.source_uri, "
        "n.id AS node_id, n.slug AS node_slug, n.status AS node_status "
        "FROM documents d "
        "JOIN node_sources s ON s.document_id = d.id AND s.project_id = d.project_id "
        "JOIN nodes n ON n.id = s.node_id AND n.project_id = s.project_id "
        "AND n.node_type = 'citation' "
        "WHERE d.project_id = %s ORDER BY n.slug",
        (pid,),
    ).fetchall()
    matches: dict[str, DocumentRef] = {}
    for row in rows:
        # A citation slug encodes exactly one document's sha256.  A citation
        # node can source other documents (merges, seed artifacts); letting its
        # slug identify all of them makes targets ambiguous.
        node_slug = (
            row["node_slug"] if row["node_slug"] == citation_node_slug(str(row["sha256"])) else None
        )
        identifiers = document_identifiers(
            sha256=row["sha256"], source_uri=row["source_uri"], node_slug=node_slug
        )
        candidates = set(identifiers)
        candidates.update(value.removeprefix("source:") for value in identifiers)
        if needle not in candidates:
            continue
        matches[str(row["document_id"])] = DocumentRef(
            project_id=pid,
            document_id=str(row["document_id"]),
            sha256=str(row["sha256"]),
            source_uri=str(row["source_uri"]),
            node_id=str(row["node_id"]),
            node_slug=str(row["node_slug"]),
            status=str(row["node_status"]),
        )
    if not matches:
        raise ValueError(f"no compiled document matches {target!r} in project {pid}")
    if len(matches) > 1:
        raise ValueError(f"document target {target!r} is ambiguous in project {pid}")
    return next(iter(matches.values()))


def _claim_ids(conn: psycopg.Connection, ref: DocumentRef) -> list[str]:
    """Every claim a node deletion would cascade away.

    Claims are deleted by ``citation_node_id`` (the FK cascade), not only by
    ``document_id``.  A merged citation node can anchor claims whose provenance
    document differs from the resolved one; those claims' ledger blocks are
    equally cascade-deleted, so the duplicate guard must see them too.
    """

    rows = conn.execute(
        "SELECT id::text AS id FROM claims WHERE project_id = %s "
        "AND (document_id = %s OR citation_node_id = %s) ORDER BY id",
        (ref.project_id, ref.document_id, ref.node_id),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _duplicate_blockers(
    conn: psycopg.Connection, ref: DocumentRef, claim_ids: list[str]
) -> list[str]:
    """Reasons a hard delete is unsafe (another page cites it, or ledger risk)."""

    blockers: list[str] = []
    ledgered = conn.execute(
        "SELECT count(*) AS n FROM ledger_blocks WHERE project_id = %s "
        "AND (node_id = %s OR claim_id = ANY(%s::uuid[]))",
        (ref.project_id, ref.node_id, claim_ids),
    ).fetchone()["n"]
    if ledgered:
        blockers.append(f"{ledgered} append-only ledger block(s) anchor at the node or its claims")

    citing = conn.execute(
        "SELECT DISTINCT n.slug AS slug FROM node_sources s "
        "JOIN nodes n ON n.id = s.node_id AND n.project_id = s.project_id "
        "WHERE s.project_id = %s AND s.document_id = %s "
        "AND n.id <> %s AND n.status = 'active' ORDER BY n.slug",
        (ref.project_id, ref.document_id, ref.node_id),
    ).fetchall()
    for row in citing:
        blockers.append(f"active node {row['slug']} sources the document")

    ledger_citers = conn.execute(
        "SELECT DISTINCT n.slug AS slug FROM ledger_blocks b "
        "JOIN nodes n ON n.id = b.node_id AND n.project_id = b.project_id "
        "WHERE b.project_id = %s AND b.claim_id = ANY(%s::uuid[]) "
        "AND b.node_id <> %s AND n.status = 'active' ORDER BY n.slug",
        (ref.project_id, claim_ids, ref.node_id),
    ).fetchall()
    for row in ledger_citers:
        blockers.append(f"active node {row['slug']} carries a ledger block for its claims")

    edge_citers = conn.execute(
        "SELECT n.slug AS slug FROM edges e "
        "JOIN nodes n ON n.id = (CASE WHEN e.src_node = %s::uuid "
        "THEN e.dst_node ELSE e.src_node END) AND n.project_id = e.project_id "
        "WHERE e.project_id = %s AND (e.src_node = %s::uuid OR e.dst_node = %s::uuid) "
        "AND n.id <> %s::uuid AND n.status = 'active' ORDER BY n.slug",
        (ref.node_id, ref.project_id, ref.node_id, ref.node_id, ref.node_id),
    ).fetchall()
    for row in edge_citers:
        blockers.append(f"active node {row['slug']} is connected by an edge")
    return blockers


def _append_removal_version(
    conn: psycopg.Connection, project_id: UUID | str, node_id: str, status: str
) -> None:
    """Record a status change as a new immutable node version (Ruling 14)."""

    pid, node = str(project_id), str(node_id)
    text = render_node(conn, pid, node)
    row = conn.execute(
        "SELECT coalesce(max(version), 0) + 1 AS next_version "
        "FROM node_versions WHERE node_id = %s AND project_id = %s",
        (node, pid),
    ).fetchone()
    version = conn.execute(
        "INSERT INTO node_versions (project_id, node_id, version, rendered_text) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (pid, node, int(row["next_version"]), text),
    ).fetchone()
    conn.execute(
        "UPDATE nodes SET status = %s, current_version_id = %s "
        "WHERE id = %s::uuid AND project_id = %s",
        (status, str(version["id"]), node, pid),
    )


def _merge(report: IntegrityReport, other: IntegrityReport) -> None:
    for key, value in other.counts.items():
        report.set_count(key, value)
    report.findings.extend(other.findings)
    report.warnings.extend(other.warnings)
    report.rows.extend(other.rows)


def _limited_findings(report: IntegrityReport, key: str, label: str, values: list[str]) -> None:
    report.set_count(key, len(values))
    for value in values[:MAX_EXAMPLES]:
        report.finding(f"{label}: {value}")
    if len(values) > MAX_EXAMPLES:
        report.finding(f"{label}: {len(values) - MAX_EXAMPLES} more (truncated)")


def _claim_support(conn: psycopg.Connection, pid: str) -> dict[str, set[str]]:
    """Provenance documents for each claim — never inferred from co-citation.

    A claim's support is its own provenance document plus the documents of its
    citation node.  Expanding through every document that happens to source a
    ledger or edge endpoint would manufacture support: a widely-sourced entity
    node would mask a retracted-only claim and defeat D7 lint.
    """

    support: dict[str, set[str]] = {}

    def add(claim_id: object, document_id: object) -> None:
        support.setdefault(str(claim_id), set()).add(str(document_id))

    for row in conn.execute(
        "SELECT id::text AS id, document_id::text AS document_id FROM claims WHERE project_id = %s",
        (pid,),
    ).fetchall():
        add(row["id"], row["document_id"])
    for row in conn.execute(
        "SELECT c.id::text AS claim_id, s.document_id::text AS document_id "
        "FROM claims c JOIN node_sources s "
        "ON s.node_id = c.citation_node_id AND s.project_id = c.project_id "
        "WHERE c.project_id = %s",
        (pid,),
    ).fetchall():
        add(row["claim_id"], row["document_id"])
    return support


def _document_statuses(conn: psycopg.Connection, pid: str) -> dict[str, set[str]]:
    """All citation-node statuses of each document (a document may have many)."""

    statuses: dict[str, set[str]] = {}
    rows = conn.execute(
        "SELECT DISTINCT d.id::text AS document_id, n.status AS status "
        "FROM documents d "
        "JOIN node_sources s ON s.document_id = d.id AND s.project_id = d.project_id "
        "JOIN nodes n ON n.id = s.node_id AND n.project_id = s.project_id "
        "AND n.node_type = 'citation' "
        "WHERE d.project_id = %s",
        (pid,),
    ).fetchall()
    for row in rows:
        statuses.setdefault(str(row["document_id"]), set()).add(str(row["status"]))
    return statuses


def _support_class(document_id: str, statuses: dict[str, set[str]]) -> str:
    """retracted / archived / active — all citation nodes must agree."""

    states = statuses.get(document_id, set())
    if not states:
        return "active"
    if all(state == "retracted" for state in states):
        return "retracted"
    if all(state in REMOVED_STATUSES for state in states):
        return "archived"
    return "active"


def scan_leaks(
    conn: psycopg.Connection, project_id: UUID | str, *, flag_retracted: bool = False
) -> IntegrityReport:
    """Post-removal leak scan: active content still citing removed documents.

    ``leaks.total`` counts only *active* nodes/edges/ledger rows that still
    reference a removed document.  Claims whose whole support set is removed
    are an expected consequence of tombstoning and are counted (and, for
    retracted-only claims, warned about) without failing the command.
    """

    report = IntegrityReport("leaks")
    pid = str(project_id)
    rows = conn.execute(
        "SELECT DISTINCT d.id::text AS document_id, n.status AS status "
        "FROM documents d "
        "JOIN node_sources s ON s.document_id = d.id AND s.project_id = d.project_id "
        "JOIN nodes n ON n.id = s.node_id AND n.project_id = s.project_id "
        "AND n.node_type = 'citation' "
        "WHERE d.project_id = %s AND n.status IN ('archived', 'retracted')",
        (pid,),
    ).fetchall()
    removed = {str(row["document_id"]): str(row["status"]) for row in rows}
    report.set_count("documents.removed", len(removed))
    for key in ("leaks.active_nodes", "leaks.active_edges", "leaks.ledger_claims"):
        report.set_count(key, 0)
    report.set_count("claims.removed_only", 0)
    report.set_count("claims.retracted_only", 0)
    if not removed:
        report.set_count("leaks.total", 0)
        return report

    document_ids = list(removed)
    node_rows = conn.execute(
        "SELECT n.slug AS slug, s.document_id::text AS document_id "
        "FROM node_sources s JOIN nodes n "
        "ON n.id = s.node_id AND n.project_id = s.project_id "
        "WHERE s.project_id = %s AND n.status = 'active' AND s.document_id = ANY(%s::uuid[]) "
        "ORDER BY n.slug",
        (pid, document_ids),
    ).fetchall()
    _limited_findings(
        report,
        "leaks.active_nodes",
        "active node still sources a removed document",
        [f"{row['slug']} <- {row['document_id']}" for row in node_rows],
    )

    edge_rows = conn.execute(
        "SELECT DISTINCT e.id::text AS edge_id, c.id::text AS claim_id "
        "FROM edges e "
        "JOIN nodes src ON src.id = e.src_node AND src.project_id = e.project_id "
        "JOIN nodes dst ON dst.id = e.dst_node AND dst.project_id = e.project_id "
        "JOIN edge_sources es ON es.edge_id = e.id AND es.project_id = e.project_id "
        "JOIN claims c ON c.id = es.claim_id AND c.project_id = e.project_id "
        "WHERE e.project_id = %s AND c.document_id = ANY(%s::uuid[]) "
        "AND src.status = 'active' AND dst.status = 'active' "
        "ORDER BY edge_id, claim_id",
        (pid, document_ids),
    ).fetchall()
    _limited_findings(
        report,
        "leaks.active_edges",
        "active edge still cites a claim from a removed document",
        [f"{row['edge_id']} -> {row['claim_id']}" for row in edge_rows],
    )

    ledger_rows = conn.execute(
        "SELECT DISTINCT n.slug AS slug, b.claim_id::text AS claim_id "
        "FROM ledger_blocks b "
        "JOIN nodes n ON n.id = b.node_id AND n.project_id = b.project_id "
        "JOIN claims c ON c.id = b.claim_id AND c.project_id = b.project_id "
        "WHERE b.project_id = %s AND n.status = 'active' "
        "AND c.document_id = ANY(%s::uuid[]) ORDER BY slug, claim_id",
        (pid, document_ids),
    ).fetchall()
    _limited_findings(
        report,
        "leaks.ledger_claims",
        "active node still carries a ledger claim from a removed document",
        [f"{row['slug']} -> {row['claim_id']}" for row in ledger_rows],
    )

    statuses = _document_statuses(conn, pid)
    for claim_id, documents in sorted(_claim_support(conn, pid).items()):
        if not documents:
            continue
        classes = {_support_class(document, statuses) for document in documents}
        if not classes <= {"retracted", "archived"}:
            continue
        report.count("claims.removed_only", 1)
        if classes == {"retracted"}:
            report.count("claims.retracted_only", 1)
            if flag_retracted:
                report.warning(f"claim {claim_id} is supported only by retracted documents")
    report.set_count(
        "leaks.total",
        report.counts["leaks.active_nodes"]
        + report.counts["leaks.active_edges"]
        + report.counts["leaks.ledger_claims"],
    )
    return report


def remove_document(
    conn: psycopg.Connection,
    project_id: UUID | str,
    target: str,
    mode: str,
) -> IntegrityReport:
    """Apply one removal end to end and scan for leaks afterwards."""

    report = IntegrityReport("removal")
    report.set_count("removals.attempted", 1)
    pid = str(project_id)
    if mode not in REMOVAL_MODES:
        report.finding(f"unknown removal mode {mode!r}; expected one of {', '.join(REMOVAL_MODES)}")
        report.set_count("removals.failed", 1)
        return report
    try:
        initial = resolve_document(conn, project_id, target)
    except ValueError as exc:
        report.finding(str(exc))
        report.set_count("removals.failed", 1)
        return report

    with with_writer_lock(project_id, conn):
        job_id = create_job(
            conn,
            project_id,
            JOB_KIND_REMOVAL,
            {
                "target": str(target),
                "mode": mode,
                "document_id": initial.document_id,
                "node_id": initial.node_id,
            },
        )

        def refuse(message: str) -> IntegrityReport:
            report.finding(message)
            report.set_count("removals.failed", 1)
            emit_event(
                conn,
                project_id,
                REMOVAL_REFUSED,
                {"document_id": initial.document_id, "mode": mode, "reason": message},
            )
            set_job_state(conn, job_id, project_id, "failed", {"reason": message})
            return report

        try:
            ref = resolve_document(conn, project_id, target)
        except ValueError as exc:
            return refuse(str(exc))
        if ref.status in REMOVED_STATUSES:
            return refuse(f"document {ref.sha256} is already {ref.status}")

        claim_ids = _claim_ids(conn, ref)
        block_ids: list[str] = []
        if mode == MODE_DUPLICATE:
            blockers = _duplicate_blockers(conn, ref, claim_ids)
            if blockers:
                for blocker in blockers:
                    report.finding(f"duplicate removal refused: {blocker}")
                return refuse(f"duplicate removal unsafe for {ref.sha256}")

            deleted = conn.execute(
                "DELETE FROM edges WHERE project_id = %s "
                "AND (src_node = %s::uuid OR dst_node = %s::uuid)",
                (pid, ref.node_id, ref.node_id),
            )
            report.set_count("edges.deleted", deleted.rowcount)
            conn.execute(
                "DELETE FROM nodes WHERE id = %s::uuid AND project_id = %s",
                (ref.node_id, pid),
            )
            report.set_count("nodes.deleted", 1)
            final_status = "deleted"
        else:
            status = STATUS_FOR_MODE[mode]
            _append_removal_version(conn, project_id, ref.node_id, status)
            report.set_count("versions.appended", 1)
            # Append-only derived anchor: records the removal without breaking
            # the hash chain (nothing is deleted on this path).
            block_ids = [
                str(block) for block in append_ledger_batch(conn, project_id, ref.node_id, [])
            ]
            report.set_count("ledger.blocks_appended", len(block_ids))
            final_status = status

        emit_event(
            conn,
            project_id,
            REMOVAL_COMPLETED,
            {
                "document_id": ref.document_id,
                "sha256": ref.sha256,
                "node_id": ref.node_id,
                "mode": mode,
                "status": final_status,
                "ledger_block_ids": block_ids,
            },
        )
        set_job_state(conn, job_id, project_id, "done", {"status": final_status})

    report.set_count("removals.done", 1)
    # D7: the removal path itself flags retracted-only claims; the read-only
    # `leaks` command is for re-running the same scan later.
    _merge(report, scan_leaks(conn, project_id, flag_retracted=True))
    leaks = report.counts.get("leaks.total", 0)
    if leaks:
        report.warning(
            f"removal is committed (job done) but {leaks} active leak(s) remain; "
            "the non-zero exit code reports the leaks, not a failed removal"
        )
    return report
