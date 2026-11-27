"""Read-only project-scoped queries for the interface.

Every query is filtered by the resolved project id. Nothing in this module
writes to the database, so the Documents and citation pages are safe to call
while a compile job holds the project writer lock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from ..compile.graph import resolve_project
from ..db.connection import connect
from ..qa.pipeline import document_slug
from ..qa.prompts import claim_ref
from .config import WebConfig
from .models import ClaimDetail, DocumentSummary, SelectableFile

_SELECTABLE_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".pdf"})


class Repository(Protocol):
    """The reading surface the pages depend on (easy to fake in tests)."""

    def list_documents(self) -> list[DocumentSummary]: ...

    def claim_detail(self, claim_id: str) -> ClaimDetail | None: ...

    def selectable_files(self) -> list[SelectableFile]: ...


class SqlRepository:
    """Postgres implementation of :class:`Repository`."""

    def __init__(self, config: WebConfig) -> None:
        self.config = config

    def _connection(self) -> psycopg.Connection:
        return connect(self.config.db_dsn, row_factory=dict_row, autocommit=True)

    def list_documents(self) -> list[DocumentSummary]:
        with self._connection() as conn:
            project = resolve_project(conn, self.config.project_ref)
            rows = conn.execute(
                """
                SELECT d.id, d.sha256, d.source_uri, d.parser,
                       (SELECT count(*) FROM claims c
                         WHERE c.project_id = d.project_id AND c.document_id = d.id)
                       AS claim_count
                FROM documents d
                WHERE d.project_id = %s
                ORDER BY d.source_uri, d.id
                """,
                (project.id,),
            ).fetchall()
        return [
            DocumentSummary(
                id=str(row["id"]),
                title=Path(str(row["source_uri"]).replace("\\", "/")).stem,
                slug=document_slug(str(row["source_uri"])),
                claim_count=int(row["claim_count"]),
                sha256=str(row["sha256"]),
                source_uri=str(row["source_uri"]),
                parser=str(row["parser"]),
            )
            for row in rows
        ]

    def claim_detail(self, claim_id: str) -> ClaimDetail | None:
        try:
            canonical = UUID(claim_id)
        except ValueError:
            return None
        with self._connection() as conn:
            project = resolve_project(conn, self.config.project_ref)
            row = conn.execute(
                """
                SELECT c.id, c.text, c.anchor_quote, d.source_uri, n.status
                FROM claims c
                JOIN documents d
                  ON d.id = c.document_id AND d.project_id = c.project_id
                JOIN nodes n
                  ON n.id = c.citation_node_id AND n.project_id = c.project_id
                WHERE c.project_id = %s AND c.id = %s
                """,
                (project.id, canonical),
            ).fetchone()
        if row is None:
            return None
        source_uri = str(row["source_uri"])
        return ClaimDetail(
            claim_id=str(row["id"]),
            ref=claim_ref(canonical),
            text=str(row["text"]),
            anchor_quote=str(row["anchor_quote"]),
            document_slug=document_slug(source_uri),
            document_title=Path(source_uri.replace("\\", "/")).stem,
            source_uri=source_uri,
            status=str(row["status"]),
        )

    def selectable_files(self) -> list[SelectableFile]:
        """Files already in the sandbox upload directory, newest name last."""

        directory = self.config.upload_dir
        if not directory.is_dir():
            return []
        files = [
            SelectableFile(path=path, name=path.name, size=path.stat().st_size)
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in _SELECTABLE_SUFFIXES
        ]
        return sorted(files, key=lambda item: item.name)
