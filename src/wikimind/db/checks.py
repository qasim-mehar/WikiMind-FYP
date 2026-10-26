"""Database invariant checks used by ``wikimind db check``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import sql

from wikimind.config import redact
from wikimind.db.connection import DatabaseNotConfigured, connect
from wikimind.db.migrations import verify_migration_hashes

EXPECTED_TABLES = (
    "documents",
    "channels",
    "nodes",
    "node_versions",
    "node_redirects",
    "claims",
    "ledger_blocks",
    "edges",
    "edge_sources",
    "node_sources",
    "embeddings",
    "jobs",
    "cost_events",
    "events",
)

ENUM_COLUMNS: dict[tuple[str, str], tuple[str, ...]] = {
    ("nodes", "node_type"): ("citation", "entity", "concept", "synthetic", "memory"),
    ("nodes", "status"): ("active", "stale", "superseded", "archived", "retracted"),
    ("ledger_blocks", "block_type"): ("claim", "derived"),
    (
        "edges",
        "edge_type",
    ): (
        "relates_to",
        "part_of",
        "supports",
        "contradicts",
        "supersedes",
        "cites",
        "authored_by",
        "uses",
        "measured_by",
    ),
    ("jobs", "state"): ("queued", "running", "done", "failed", "crashed"),
}

ORPHAN_QUERIES: dict[str, str] = {
    "claims": """
        SELECT count(*)
        FROM claims c
        LEFT JOIN nodes n ON n.id = c.citation_node_id
        LEFT JOIN channels ch ON ch.id = c.channel_id AND ch.document_id = c.document_id
        WHERE n.id IS NULL
           OR ch.id IS NULL
           OR n.project_id <> c.project_id
           OR ch.project_id <> c.project_id
    """,
    "ledger_blocks": """
        SELECT count(*)
        FROM ledger_blocks b
        LEFT JOIN nodes n ON n.id = b.node_id
        LEFT JOIN claims c ON c.id = b.claim_id
        WHERE n.id IS NULL
           OR (b.claim_id IS NOT NULL AND c.id IS NULL)
           OR n.project_id <> b.project_id
           OR (b.claim_id IS NOT NULL AND c.project_id <> b.project_id)
    """,
    "node_versions": """
        SELECT count(*)
        FROM node_versions v
        LEFT JOIN nodes n ON n.id = v.node_id
        WHERE n.id IS NULL OR n.project_id <> v.project_id
    """,
    "edge_sources": """
        SELECT count(*)
        FROM edge_sources s
        LEFT JOIN edges e ON e.id = s.edge_id
        LEFT JOIN claims c ON c.id = s.claim_id
        WHERE e.id IS NULL
           OR c.id IS NULL
           OR e.project_id <> s.project_id
           OR c.project_id <> s.project_id
    """,
    "node_sources": """
        SELECT count(*)
        FROM node_sources s
        LEFT JOIN nodes n ON n.id = s.node_id
        LEFT JOIN documents d ON d.id = s.document_id
        WHERE n.id IS NULL
           OR d.id IS NULL
           OR n.project_id <> s.project_id
           OR d.project_id <> s.project_id
    """,
}


@dataclass(frozen=True, slots=True)
class Finding:
    level: str
    code: str
    message: str


@dataclass
class CheckReport:
    findings: list[Finding] = field(default_factory=list)
    pgvector_version: str | None = None

    def error(self, code: str, message: str) -> None:
        self.findings.append(Finding("error", code, message))

    def warning(self, code: str, message: str) -> None:
        self.findings.append(Finding("warning", code, message))

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.level == "warning"]

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 2
        if self.warnings:
            return 1
        return 0

    @property
    def ok(self) -> bool:
        return not self.errors


def _table_project_id_state(conn: psycopg.Connection) -> dict[str, bool | None]:
    schema = conn.execute("SELECT current_schema()").fetchone()
    schema_name = schema[0] if schema else None
    if not schema_name:
        return {}

    rows = conn.execute(
        """
        SELECT c.relname, a.attnotnull
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attribute a
          ON a.attrelid = c.oid
         AND a.attname = 'project_id'
         AND a.attnum > 0
         AND NOT a.attisdropped
        WHERE n.nspname = %s
          AND c.relkind = 'r'
        """,
        (schema_name,),
    ).fetchall()
    return {str(row[0]): (None if row[1] is None else bool(row[1])) for row in rows}


def _check_expected_tables(conn: psycopg.Connection, report: CheckReport) -> bool:
    state = _table_project_id_state(conn)
    complete = True

    for table in ("projects", "schema_migrations"):
        if table not in state:
            report.error("schema.missing_table", f"required table is missing: {table}")
            complete = False

    for table in EXPECTED_TABLES:
        if table not in state:
            report.error("schema.missing_table", f"expected table is missing: {table}")
            complete = False
            continue
        not_null = state[table]
        if not_null is None:
            report.error(
                "schema.project_id_missing",
                f"{table} has no project_id column",
            )
            complete = False
        elif not not_null:
            report.error(
                "schema.project_id_nullable",
                f"{table}.project_id must be NOT NULL",
            )

    return complete


def _check_orphans(conn: psycopg.Connection, report: CheckReport) -> None:
    for table, query in ORPHAN_QUERIES.items():
        try:
            count = int(conn.execute(query).fetchone()[0])
        except psycopg.Error as exc:
            report.error(
                "orphan.query_failed",
                f"could not check orphan {table}: {redact(exc)}",
            )
            continue
        if count:
            report.error(
                "orphan.rows",
                f"{table} has {count} orphan row(s)",
            )


def _check_edge_provenance(conn: psycopg.Connection, report: CheckReport) -> None:
    try:
        count = int(
            conn.execute(
                """
                SELECT count(*)
                FROM edges e
                WHERE NOT EXISTS (
                    SELECT 1 FROM edge_sources s WHERE s.edge_id = e.id
                )
                """
            ).fetchone()[0]
        )
    except psycopg.Error as exc:
        report.error(
            "edge.query_failed",
            f"could not check edge provenance: {redact(exc)}",
        )
        return
    if count:
        report.error(
            "edge.provenance_missing",
            f"{count} edge(s) have no edge_sources row",
        )


def _check_claim_document_provenance(conn: psycopg.Connection, report: CheckReport) -> None:
    """Every claim's channel must belong to its citation node's document.

    The composite FK guarantees the claim's ``document_id`` matches its
    channel; the remaining question is whether the citation node is actually
    sourced from that document via ``node_sources``.
    """

    try:
        count = int(
            conn.execute(
                """
                SELECT count(*)
                FROM claims c
                WHERE NOT EXISTS (
                    SELECT 1 FROM node_sources s
                    WHERE s.node_id = c.citation_node_id
                      AND s.document_id = c.document_id
                )
                """
            ).fetchone()[0]
        )
    except psycopg.Error as exc:
        report.error(
            "claims.query_failed",
            f"could not check claim document provenance: {redact(exc)}",
        )
        return
    if count:
        report.error(
            "claims.channel_document_mismatch",
            f"{count} claim(s) cite a channel from a document their citation "
            "node is not sourced from",
        )


def pgvector_version(conn: psycopg.Connection) -> str | None:
    """Return the installed ``vector`` extension version, or None."""

    row = conn.execute("SELECT ext_version FROM pg_extension WHERE extname = 'vector'").fetchone()
    return str(row[0]) if row else None


def _check_enums(conn: psycopg.Connection, report: CheckReport) -> None:
    for (table, column), values in ENUM_COLUMNS.items():
        query = sql.SQL("SELECT count(*) FROM {} WHERE {} IS NULL OR {} NOT IN ({})").format(
            sql.Identifier(table),
            sql.Identifier(column),
            sql.Identifier(column),
            sql.SQL(", ").join(sql.Literal(value) for value in values),
        )
        try:
            count = int(conn.execute(query).fetchone()[0])
        except psycopg.Error as exc:
            report.error(
                "enum.query_failed",
                f"could not check {table}.{column}: {redact(exc)}",
            )
            continue
        if count:
            report.error(
                "enum.invalid_value",
                f"{table}.{column} contains {count} value(s) outside its domain",
            )


def _check_embeddings(conn: psycopg.Connection, report: CheckReport) -> None:
    schema = conn.execute("SELECT current_schema()").fetchone()
    schema_name = schema[0] if schema else None
    column_type = conn.execute(
        """
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relname = 'embeddings'
          AND a.attname = 'vec'
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
        (schema_name,),
    ).fetchone()
    if column_type is None:
        report.error("embeddings.column_missing", "embeddings.vec is missing")
        return
    if column_type[0] != "vector(384)":
        report.error(
            "embeddings.column_type",
            f"embeddings.vec must be vector(384), found {column_type[0]}",
        )

    try:
        rows = conn.execute("SELECT id, vector_dims(vec) FROM embeddings LIMIT 100").fetchall()
    except psycopg.Error as exc:
        report.error(
            "embeddings.sample_failed",
            f"could not sample embeddings: {redact(exc)}",
        )
        return

    bad = [row[0] for row in rows if row[1] is None or int(row[1]) != 384]
    if bad:
        report.error(
            "embeddings.dimensions",
            f"{len(bad)} sampled embedding(s) are not 384-dimensional",
        )


def run_checks(
    conn: psycopg.Connection,
    migrations_dir: str | Path | None = None,
) -> CheckReport:
    """Run all schema and invariant checks against an open connection."""

    report = CheckReport()
    tables_complete = _check_expected_tables(conn, report)

    for problem in verify_migration_hashes(conn, migrations_dir):
        report.error("migrations.integrity", problem)

    try:
        report.pgvector_version = pgvector_version(conn)
    except psycopg.Error:
        report.pgvector_version = None

    if not tables_complete:
        return report

    _check_orphans(conn, report)
    _check_edge_provenance(conn, report)
    _check_claim_document_provenance(conn, report)
    _check_enums(conn, report)
    _check_embeddings(conn, report)
    return report


def check_database(
    dsn: str | None = None,
    migrations_dir: str | Path | None = None,
) -> CheckReport:
    """Connect and run checks, returning a report even when connection fails."""

    report = CheckReport()
    try:
        conn = connect(dsn)
    except (psycopg.Error, DatabaseNotConfigured, ValueError, OSError) as exc:
        report.error("db.connect", f"cannot connect: {redact(exc)}")
        return report

    try:
        report.findings.extend(run_checks(conn, migrations_dir).findings)
    except (psycopg.Error, ValueError, OSError) as exc:
        report.error("check.failed", f"check failed: {redact(exc)}")
    finally:
        conn.close()
    return report
