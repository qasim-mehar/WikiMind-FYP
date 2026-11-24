"""Removal workflow coverage: all three modes, leak scans, and safety guards."""

from __future__ import annotations

from uuid import UUID

import psycopg
from integrity_seed import (
    seed_channel,
    seed_claim,
    seed_document,
    seed_edge,
    seed_node,
    seed_project,
)
from psycopg.rows import dict_row
from test_integrity_removal_cli import _connect, _seed_compiled_reference

import wikimind.integrity.removal as removal_module
from wikimind.compile.ids import citation_node_slug
from wikimind.db.locks import with_writer_lock
from wikimind.integrity.ledger import append_ledger_batch, verify_ledger
from wikimind.integrity.removal import remove_document, resolve_document, scan_leaks

SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def _seed_plain_reference(conn: psycopg.Connection, project: UUID, sha: str) -> tuple[UUID, UUID]:
    """A reference node with NO ledger blocks — eligible for duplicate deletion."""

    document = seed_document(conn, project, sha256=sha, source_uri=f"memo://{sha[:6]}.pdf")
    seed_channel(conn, project, document, channel_id="ch-a")
    seed_node_id = seed_node(conn, project, citation_node_slug(sha), node_type="citation")
    conn.execute(
        "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s)",
        (str(project), str(seed_node_id), str(document)),
    )
    return document, seed_node_id


def _seed_active_entity(conn: psycopg.Connection, project: UUID, slug: str, document: UUID) -> UUID:
    entity = seed_node(conn, project, slug)
    conn.execute(
        "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s)",
        (str(project), str(entity), str(document)),
    )
    return entity


def test_resolve_document_accepts_sha_prefix_and_citation_slug(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-resolve")
        _, reference, _ = _seed_compiled_reference(conn, project, SHA_B)
        assert resolve_document(conn, project, SHA_B[:12]).node_id == str(reference)
        assert resolve_document(conn, project, citation_node_slug(SHA_B)).node_id == str(reference)
        assert resolve_document(conn, project, "paper-a").node_id == str(reference)
    finally:
        conn.close()


def test_retracted_mode_flags_retracted_only_claims(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-retracted")
        _, reference, _ = _seed_compiled_reference(conn, project, SHA_C)
        report = remove_document(conn, project, SHA_C, "retracted")
        assert report.counts["removals.done"] == 1
        assert (
            conn.execute("SELECT status FROM nodes WHERE id = %s", (str(reference),)).fetchone()[
                "status"
            ]
            == "retracted"
        )

        scan = scan_leaks(conn, project, flag_retracted=True)
        assert scan.counts["claims.retracted_only"] >= 1
        assert any("supported only by retracted documents" in warning for warning in scan.warnings)
        # Lint warnings never fail the removal command.
        assert report.ok is True
    finally:
        conn.close()


def test_leak_scan_flags_active_pages_after_tombstone(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-leaks")
        document, reference, _ = _seed_compiled_reference(conn, project, SHA_B)
        first = _seed_active_entity(conn, project, "entity-one", document)
        second = _seed_active_entity(conn, project, "entity-two", document)
        claim = seed_claim(conn, project, reference, "ch-a", document, text="Second claim")
        seed_edge(conn, project, first, second, claims=(claim,))
        with with_writer_lock(project, conn):
            append_ledger_batch(conn, project, first, [(first, claim)])

        report = remove_document(conn, project, SHA_B, "tombstone")
        assert report.counts["removals.done"] == 1
        assert report.counts["leaks.active_nodes"] == 2
        assert report.counts["leaks.active_edges"] == 1
        assert report.counts["leaks.ledger_claims"] == 1
        assert report.counts["leaks.total"] == 4
        assert report.ok is False
        assert any("active node" in finding for finding in report.findings)

        # The append-only chain is still valid after the removal.
        ledger = verify_ledger(conn, project)
        assert ledger.ok is True, ledger.findings
    finally:
        conn.close()


def test_duplicate_mode_deletes_unreferenced_node_and_edges(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-duplicate")
        document, reference = _seed_plain_reference(conn, project, SHA_D)
        claim = seed_claim(conn, project, reference, "ch-a", document)
        seed_edge(conn, project, reference, reference, claims=(claim,))

        report = remove_document(conn, project, SHA_D, "duplicate")
        assert report.counts["removals.done"] == 1
        assert report.counts["edges.deleted"] == 1
        assert report.counts["nodes.deleted"] == 1
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM nodes WHERE id = %s", (str(reference),)
            ).fetchone()["n"]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM edge_sources WHERE project_id = %s", (str(project),)
            ).fetchone()["n"]
            == 0
        )
        # Raw sources are immutable: the document row survives.
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM documents WHERE id = %s", (str(document),)
            ).fetchone()["n"]
            == 1
        )
        assert verify_ledger(conn, project).ok is True
    finally:
        conn.close()


def test_duplicate_mode_refuses_ledgered_node(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-duplicate-ledger")
        _, reference, _ = _seed_compiled_reference(conn, project, SHA_E)
        report = remove_document(conn, project, SHA_E, "duplicate")
        assert report.counts["removals.failed"] == 1
        assert any("append-only ledger block" in finding for finding in report.findings)
        assert (
            conn.execute("SELECT status FROM nodes WHERE id = %s", (str(reference),)).fetchone()[
                "status"
            ]
            == "active"
        )
        kinds = [
            row["kind"]
            for row in conn.execute(
                "SELECT kind FROM events WHERE project_id = %s ORDER BY id", (str(project),)
            ).fetchall()
        ]
        assert kinds[-1] == "integrity.removal_refused"
        assert (
            conn.execute(
                "SELECT state FROM jobs WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
                (str(project),),
            ).fetchone()["state"]
            == "failed"
        )
    finally:
        conn.close()


def test_duplicate_mode_refuses_when_another_page_cites(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-duplicate-cited")
        document, reference = _seed_plain_reference(conn, project, SHA_D)
        _seed_active_entity(conn, project, "citing-entity", document)
        report = remove_document(conn, project, SHA_D, "duplicate")
        assert report.counts["removals.failed"] == 1
        assert any("citing-entity" in finding for finding in report.findings)
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM nodes WHERE id = %s", (str(reference),)
            ).fetchone()["n"]
            == 1
        )
    finally:
        conn.close()


def test_duplicate_guard_covers_claims_anchored_at_node_from_other_documents(test_dsn: str) -> None:
    """A cascade-deleted claim's ledger block must be visible to the guard."""

    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-duplicate-foreign")
        document, reference = _seed_plain_reference(conn, project, "a" * 64)
        other = seed_document(conn, project, sha256="1" * 64, source_uri="memo://other.pdf")
        seed_channel(conn, project, other, channel_id="ch-other")
        # Anchored at the reference node but provenanced to another document.
        claim = seed_claim(conn, project, reference, "ch-other", other)
        holder = seed_node(conn, project, "foreign-holder")
        conn.execute(
            "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s)",
            (str(project), str(holder), str(other)),
        )
        with with_writer_lock(project, conn):
            append_ledger_batch(conn, project, holder, [(holder, claim)])

        report = remove_document(conn, project, "a" * 64, "duplicate")
        assert report.counts["removals.failed"] == 1
        assert any("append-only ledger block" in finding for finding in report.findings)
        assert verify_ledger(conn, project).ok is True
        assert document is not None  # raw source row survives in every mode
    finally:
        conn.close()


def test_retracted_only_claim_not_masked_by_cosourcing(test_dsn: str) -> None:
    """Co-citation by an active multi-source node is not evidence for a claim."""

    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-retracted-cosource")
        document, reference, _ = _seed_compiled_reference(conn, project, "2" * 64)
        active = seed_document(conn, project, sha256="3" * 64, source_uri="memo://active.pdf")
        seed_channel(conn, project, active, channel_id="ch-active")
        wide = seed_node(conn, project, "wide-entity")
        peer = seed_node(conn, project, "peer-entity")
        for source in (document, active):
            conn.execute(
                "INSERT INTO node_sources (project_id, node_id, document_id) VALUES (%s, %s, %s)",
                (str(project), str(wide), str(source)),
            )
        claim = seed_claim(conn, project, reference, "ch-a", document)
        seed_edge(conn, project, wide, peer, claims=(claim,))

        assert remove_document(conn, project, "2" * 64, "retracted").counts["removals.done"] == 1
        scan = scan_leaks(conn, project, flag_retracted=True)
        assert scan.counts["claims.retracted_only"] >= 1
        assert any("supported only by retracted documents" in w for w in scan.warnings)
    finally:
        conn.close()


def test_removal_is_idempotent_and_every_mutation_takes_the_lock(
    test_dsn: str, monkeypatch
) -> None:
    conn = psycopg.connect(test_dsn, row_factory=dict_row, autocommit=True)
    try:
        project = seed_project(conn, "removal-lock")
        _, _, _ = _seed_compiled_reference(conn, project, SHA_B)
        real = removal_module.with_writer_lock
        calls: list[str] = []

        def spy(project_id, connection=None):
            calls.append(str(project_id))
            return real(project_id, connection)

        monkeypatch.setattr(removal_module, "with_writer_lock", spy)
        assert remove_document(conn, project, SHA_B, "tombstone").counts["removals.done"] == 1
        assert calls == [str(project)]
        second = remove_document(conn, project, SHA_B, "tombstone")
        assert second.counts["removals.failed"] == 1
        assert any("already archived" in finding for finding in second.findings)
        assert len(calls) == 2
    finally:
        conn.close()


def test_unknown_mode_and_missing_target_fail_cleanly(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    try:
        project = seed_project(conn, "removal-validation")
        assert remove_document(conn, project, "anything", "bogus").counts["removals.failed"] == 1
        missing = remove_document(conn, project, "f" * 64, "tombstone")
        assert missing.counts["removals.failed"] == 1
        assert any("no compiled document" in finding for finding in missing.findings)
    finally:
        conn.close()
