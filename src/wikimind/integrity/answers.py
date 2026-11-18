"""V4: answer-verification against the QA lane's JSON contract.

The entire interface is::

    {"answer": str, "sufficient": bool,
     "citations": [{"claim_id": str, "document_slug": str}]}

No QA internals are assumed.  A citation is supported when the claim exists
and the cited ``document_slug`` resolves to that claim's own provenance
document.  Because the frozen ``documents`` table has no slug column, the
resolver accepts the stable identifiers derivable from its frozen columns:
the citation node's slug (``source:<sha256>``), the full SHA-256, common
SHA-256 prefixes, the ``source_uri``, and the slugified filename stem.  All of
these are unambiguously tied to the provenance document; a value that matches
none of them is reported as unsupported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from .model import IntegrityReport


@dataclass(frozen=True, slots=True)
class Citation:
    claim_id: str
    document_slug: str


@dataclass(frozen=True, slots=True)
class AnswerDocument:
    answer: str
    sufficient: bool
    citations: tuple[Citation, ...]


def parse_answer_document(text: str) -> AnswerDocument:
    """Parse and validate the verbatim answer JSON contract."""

    try:
        raw: Any = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"invalid answer JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise TypeError("answer JSON must be an object")
    for key in ("answer", "sufficient", "citations"):
        if key not in raw:
            raise ValueError(f"answer JSON is missing {key!r}")
    if not isinstance(raw["answer"], str):
        raise TypeError("answer must be a string")
    if not isinstance(raw["sufficient"], bool):
        raise TypeError("sufficient must be a boolean")
    if not isinstance(raw["citations"], list):
        raise TypeError("citations must be a list")
    citations: list[Citation] = []
    for index, item in enumerate(raw["citations"]):
        if not isinstance(item, dict):
            raise TypeError(f"citations[{index}] must be an object")
        claim_id, slug = item.get("claim_id"), item.get("document_slug")
        if not isinstance(claim_id, str) or not isinstance(slug, str):
            raise TypeError(f"citations[{index}] needs string claim_id and document_slug")
        citations.append(Citation(claim_id=claim_id, document_slug=slug))
    return AnswerDocument(
        answer=raw["answer"],
        sufficient=raw["sufficient"],
        citations=tuple(citations),
    )


def load_answer_document(path: str | Path) -> AnswerDocument:
    return parse_answer_document(Path(path).read_text(encoding="utf-8"))


def _slugify_stem(source_uri: str) -> str:
    """Reuse the project's existing entity normalization for a filename."""

    from wikimind.compile.ids import slugify_entity

    stem = source_uri.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return slugify_entity(stem)


def document_identifiers(
    *,
    sha256: str | None,
    source_uri: str | None,
    node_slug: str | None,
    document_id: str | None,
) -> set[str]:
    """All stable identifiers a citation may use for one document."""

    values: set[str] = set()
    if sha256:
        digest = str(sha256).lower()
        for length in (8, 12, 16, len(digest)):
            values.add(digest[:length])
            values.add(f"source:{digest[:length]}")
    if node_slug:
        values.add(str(node_slug).lower())
    if source_uri:
        values.add(str(source_uri).lower())
        values.add(_slugify_stem(str(source_uri)).lower())
    if document_id:
        values.add(str(document_id).lower())
    return values


def verify_answer(
    conn: psycopg.Connection,
    answer: AnswerDocument,
    *,
    project_id: UUID | str | None = None,
) -> IntegrityReport:
    """Verify every cited claim/document pair in ``answer``."""

    report = IntegrityReport("answer")
    report.set_count("answer.sufficient", int(answer.sufficient))
    report.set_count("citations.total", len(answer.citations))
    sql = (
        "SELECT c.id AS claim_id, c.document_id, d.sha256, d.source_uri, "
        "n.slug AS node_slug FROM claims c "
        "LEFT JOIN documents d ON d.id = c.document_id AND d.project_id = c.project_id "
        "LEFT JOIN nodes n ON n.id = c.citation_node_id AND n.project_id = c.project_id "
        "WHERE c.id::text = %s"
    )
    if project_id is None:
        # Claim ids are UUIDs and globally unique.  Pass --project to make
        # every query explicitly project-scoped.
        row_params_base: tuple[object, ...] = ()
    else:
        sql += " AND c.project_id = %s"
        row_params_base = (str(project_id),)

    for citation in answer.citations:
        row = conn.execute(sql, (citation.claim_id, *row_params_base)).fetchone()
        if row is None:
            report.count("citations.unknown_claim", 1)
            report.finding(f"unknown claim_id {citation.claim_id}")
            continue
        report.count("citations.known", 1)
        identifiers = document_identifiers(
            sha256=None if row["sha256"] is None else str(row["sha256"]),
            source_uri=None if row["source_uri"] is None else str(row["source_uri"]),
            node_slug=None if row["node_slug"] is None else str(row["node_slug"]),
            document_id=None if row["document_id"] is None else str(row["document_id"]),
        )
        if citation.document_slug.strip().lower() in identifiers:
            report.count("citations.supported", 1)
        else:
            report.count("citations.unsupported", 1)
            report.finding(
                f"unsupported citation {citation.claim_id}: "
                f"document_slug {citation.document_slug!r} is not the provenance document"
            )
    return report
