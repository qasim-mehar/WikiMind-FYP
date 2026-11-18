"""Ledger chain tests: pure verifier with a fake connection plus real DB."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from integrity_seed import (
    connect_db,
    seed_channel,
    seed_claim,
    seed_document,
    seed_node,
    seed_project,
)


def _real_claims(conn, project, count: int) -> list[UUID]:
    """Create claims the FK can accept; ledger blocks must cite real claims."""

    document = seed_document(conn, project)
    channel = seed_channel(conn, project, document, channel_id=f"ch-{uuid4().hex[:10]}")
    citation = seed_node(conn, project, f"source:{document}", node_type="citation")
    return [
        seed_claim(conn, project, citation, channel, document, text=f"claim {index}")
        for index in range(count)
    ]


from wikimind.integrity.events import UNIT_COMMITTED, emit_event
from wikimind.integrity.ids import ZERO_UUID, batch_fingerprint, ledger_block_uuid
from wikimind.integrity.ledger import (
    LedgerBlock,
    append_ledger_batch,
    append_ledger_batch_locked,
    batch_already_committed,
    verify_chain,
    verify_ledger,
)


class _Cursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def fetchone(self) -> dict | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict]:
        return self.rows


class FakeConn:
    """Records INSERTs so the writer can be tested without Postgres."""

    def __init__(self) -> None:
        self.blocks: list[dict] = []

    def execute(self, sql: str, params: tuple | None = None) -> _Cursor:
        compact = " ".join(sql.split())
        if "MAX(position)" in compact:
            assert params is not None
            pid = str(params[0])
            relevant = [b for b in self.blocks if b["project_id"] == pid]
            nxt = max((b["position"] for b in relevant), default=-1) + 1
            return _Cursor([{"next_position": nxt}])
        if "ORDER BY position DESC LIMIT 1" in compact:
            assert params is not None
            pid = str(params[0])
            rows = sorted(
                (b for b in self.blocks if b["project_id"] == pid),
                key=lambda b: b["position"],
            )
            return _Cursor([{"id": rows[-1]["id"]}] if rows else [])
        if "node_id = ANY" in compact:
            return _Cursor([])
        if compact.startswith("SELECT 1 FROM ledger_blocks WHERE id"):
            assert params is not None
            found = any(b["id"] == str(params[0]) for b in self.blocks)
            return _Cursor([{"1": 1}] if found else [])
        if "INSERT INTO ledger_blocks" in compact:
            assert params is not None
            block_id, project_id, node_id, claim_id, position, block_type = params
            self.blocks.append(
                {
                    "id": str(block_id),
                    "project_id": str(project_id),
                    "node_id": str(node_id),
                    "claim_id": None if claim_id is None else str(claim_id),
                    "position": int(position),
                    "block_type": block_type,
                }
            )
            return _Cursor([])
        raise AssertionError(f"unexpected SQL: {compact}")


def _blocks(conn: FakeConn) -> list[LedgerBlock]:
    return [LedgerBlock.from_row(row) for row in conn.blocks]


def test_writer_produces_a_verifiable_chain_with_fakes() -> None:
    project, anchor = uuid4(), uuid4()
    entries = [(uuid4(), uuid4()) for _ in range(3)]
    conn = FakeConn()
    ids = append_ledger_batch(conn, project, anchor, entries)
    assert len(ids) == 1 + len(entries)
    report = verify_chain(_blocks(conn))
    assert report.ok
    assert report.counts["blocks"] == 4
    assert report.counts["derived_blocks"] == 1
    assert report.counts["claim_blocks"] == 3
    assert report.rows[0]["status"] == "valid"


def test_multiple_batches_chain_end_to_end() -> None:
    project, anchor = uuid4(), uuid4()
    conn = FakeConn()
    append_ledger_batch(conn, project, anchor, [(uuid4(), uuid4())])
    append_ledger_batch(conn, project, anchor, [(uuid4(), uuid4()), (uuid4(), uuid4())])
    report = verify_chain(_blocks(conn))
    assert report.ok
    assert report.counts["blocks"] == 5
    assert report.counts["valid_chains"] == 1


def test_tampered_membership_is_detected_at_anchor() -> None:
    project, anchor, claim = uuid4(), uuid4(), uuid4()
    conn = FakeConn()
    append_ledger_batch(conn, project, anchor, [(uuid4(), claim)])
    original = _blocks(conn)
    # Replace the claim id in block 1 while keeping its hash slot unchanged.
    tampered = [original[0], replace(original[1], claim_id=str(uuid4()))]
    report = verify_chain(tampered)
    assert not report.ok
    # The anchor commits to batch membership, so verification stops at the
    # first mismatch (the anchor, block 0) and never reaches the claim block.
    assert any("broken at block 0" in finding for finding in report.findings)
    assert report.counts["broken_chains"] == 1


def test_tampered_anchor_membership_and_position_are_detected() -> None:
    project, anchor = uuid4(), uuid4()
    node, claim = uuid4(), uuid4()
    conn = FakeConn()
    append_ledger_batch(conn, project, anchor, [(node, claim)])
    original = _blocks(conn)
    # The anchor commits to the batch membership via its id.
    swapped = [replace(original[0], node_id=str(uuid4())), original[1]]
    assert not verify_chain(swapped).ok
    # A deleted block leaves a position gap.
    assert not verify_chain([original[0], replace(original[1], position=5)]).ok


def test_two_project_chains_are_verified_independently() -> None:
    first, second = uuid4(), uuid4()
    conn = FakeConn()
    append_ledger_batch(conn, first, uuid4(), [(uuid4(), uuid4())])
    append_ledger_batch(conn, second, uuid4(), [(uuid4(), uuid4())])
    report = verify_chain(_blocks(conn))
    assert report.ok
    assert report.counts["projects"] == 2
    assert report.counts["valid_chains"] == 2


def test_empty_project_is_a_clean_ledger(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        report = verify_ledger(conn, seed_project(conn))
        assert report.ok
        assert report.counts["blocks"] == 0
    finally:
        conn.close()


def test_db_writer_and_verifier_round_trip(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        anchor = seed_node(conn, project, "source:chain-test")
        claim_ids = _real_claims(conn, project, 4)
        batch = claim_ids[:2]
        first = append_ledger_batch(conn, project, anchor, [(anchor, claim) for claim in batch])
        assert len(first) == 3
        report = verify_ledger(conn, project)
        assert report.ok, report.findings
        assert report.counts["claim_blocks"] == 2

        # Re-sending the same entries is a no-op: no phantom empty anchor.
        again = append_ledger_batch(conn, project, anchor, [(anchor, claim) for claim in batch])
        assert again == []
        assert verify_ledger(conn, project).ok

        # A standalone locked append works and keeps the chain intact.
        standalone = append_ledger_batch_locked(
            project, anchor, [(anchor, claim_ids[2])], dsn=test_dsn
        )
        assert len(standalone) == 2
        assert verify_ledger(conn, project).ok
    finally:
        conn.close()


def test_db_ledger_detects_tampering(test_dsn: str) -> None:
    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        anchor = seed_node(conn, project, "source:tamper")
        claim = _real_claims(conn, project, 1)[0]
        append_ledger_batch(conn, project, anchor, [(anchor, claim)])
        # Position has no FK, so it is a clean tamper target.
        conn.execute(
            "UPDATE ledger_blocks SET position = position + 100 WHERE block_type = 'claim' "
            "AND project_id = %s",
            (str(project),),
        )
        report = verify_ledger(conn, project)
        assert not report.ok
        assert any("broken at block" in finding for finding in report.findings)
    finally:
        conn.close()


def test_hash_inputs_use_the_documented_domain() -> None:
    project, anchor, claim = uuid4(), uuid4(), uuid4()
    expected = ledger_block_uuid(
        project_id=str(project),
        prev_id=ZERO_UUID,
        position=0,
        node_id=str(anchor),
        claim_id=None,
        block_type="derived",
        payload_digest=batch_fingerprint(str(project), str(anchor), [(str(anchor), str(claim))]),
    )
    conn = FakeConn()
    append_ledger_batch(conn, project, anchor, [(anchor, claim)])
    assert UUID(conn.blocks[0]["id"]) == expected


def test_tail_truncation_is_detected_via_events(test_dsn: str) -> None:
    """G1: a chain tail deleted from ledger_blocks alone is caught."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        anchor = seed_node(conn, project, "source:tail-truncation")
        claims = _real_claims(conn, project, 2)
        first = append_ledger_batch(conn, project, anchor, [(anchor, claims[0])])
        second = append_ledger_batch(conn, project, anchor, [(anchor, claims[1])])
        emit_event(conn, project, UNIT_COMMITTED, {"ledger_block_ids": [str(b) for b in first]})
        emit_event(conn, project, UNIT_COMMITTED, {"ledger_block_ids": [str(b) for b in second]})
        assert verify_ledger(conn, project).ok

        conn.execute(
            "DELETE FROM ledger_blocks WHERE id::text = ANY(%s)", ([str(b) for b in second],)
        )
        report = verify_ledger(conn, project)
        assert not report.ok
        assert report.counts["event_references.missing"] >= 1
        assert any("tail truncation at position 1" in finding for finding in report.findings)
        assert any(row["status"] == "broken" for row in report.rows)
    finally:
        conn.close()


def test_batch_retry_is_a_noop_without_phantom_anchors(test_dsn: str) -> None:
    """G5: retrying a committed batch writes neither anchor nor event payload."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        anchor = seed_node(conn, project, "source:retry")
        claims = _real_claims(conn, project, 2)
        entries = [(anchor, claim) for claim in claims]
        assert not batch_already_committed(conn, project, anchor, entries)
        ids = append_ledger_batch(conn, project, anchor, entries)
        assert len(ids) == 3
        assert batch_already_committed(conn, project, anchor, entries)
        assert append_ledger_batch(conn, project, anchor, entries) == []
        derived = conn.execute(
            "SELECT count(*) AS n FROM ledger_blocks WHERE project_id = %s AND block_type = 'derived'",
            (str(project),),
        ).fetchone()["n"]
        assert derived == 1
        assert verify_ledger(conn, project).ok
    finally:
        conn.close()


def test_empty_batch_retry_after_chain_advancement_is_a_noop(test_dsn: str) -> None:
    """G9: an empty batch stays idempotent after another batch advanced the chain."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        anchor = seed_node(conn, project, "source:empty-retry")
        document_a = seed_document(conn, project)
        document_b = seed_document(conn, project)
        empty = append_ledger_batch(
            conn,
            project,
            anchor,
            [],
            document_id=str(document_a),
            channel_id="empty-channel",
            compile_version="v1",
        )
        assert len(empty) == 1
        emit_event(
            conn,
            project,
            UNIT_COMMITTED,
            {
                "document_id": str(document_a),
                "channel_id": "empty-channel",
                "compile_version": "v1",
                "ledger_block_ids": [str(empty[0])],
            },
        )
        claim = _real_claims(conn, project, 1)[0]
        later = append_ledger_batch(
            conn,
            project,
            anchor,
            [(anchor, claim)],
            document_id=str(document_b),
            channel_id="other-channel",
            compile_version="v1",
        )
        assert len(later) == 2

        assert batch_already_committed(
            conn,
            project,
            anchor,
            [],
            document_id=str(document_a),
            channel_id="empty-channel",
            compile_version="v1",
        )
        assert (
            append_ledger_batch(
                conn,
                project,
                anchor,
                [],
                document_id=str(document_a),
                channel_id="empty-channel",
                compile_version="v1",
            )
            == []
        )
        derived = conn.execute(
            "SELECT count(*) AS n FROM ledger_blocks "
            "WHERE project_id = %s AND block_type = 'derived'",
            (str(project),),
        ).fetchone()["n"]
        unit_events = conn.execute(
            "SELECT count(*) AS n FROM events WHERE project_id = %s AND kind = %s "
            "AND data->>'channel_id' = %s",
            (str(project), UNIT_COMMITTED, "empty-channel"),
        ).fetchone()["n"]
        assert derived == 2
        assert unit_events == 1
        assert verify_ledger(conn, project).ok
    finally:
        conn.close()


def test_marker_does_not_collide_across_documents(test_dsn: str) -> None:
    """HF1: identical channel text in two documents yields the SAME channel_id.

    Both compile units must still get their own anchor, claim block and event.
    """

    from wikimind.parse.ids import channel_id as derive_channel_id
    from wikimind.parse.ids import normalize_text

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        document_a = seed_document(conn, project)
        document_b = seed_document(conn, project)
        shared_text = "Alpha joined Beta Labs in 2017."
        # Observed equality: both documents derive the identical 64-hex digest
        # from (kind, normalized text); migration 002 makes this expected.
        shared_cid = derive_channel_id("section", normalize_text(shared_text))
        assert shared_cid == derive_channel_id("section", normalize_text(shared_text))
        version = "v1"

        anchors: dict[str, UUID] = {}
        claims: dict[str, UUID] = {}
        for suffix, document in (("A", document_a), ("B", document_b)):
            seed_channel(conn, project, document, channel_id=shared_cid, text=shared_text)
            citation = seed_node(
                conn, project, f"source:shared-{suffix}-{uuid4().hex[:8]}", node_type="citation"
            )
            anchors[suffix] = citation
            claims[suffix] = seed_claim(
                conn,
                project,
                citation,
                shared_cid,
                document,
                text=f"Claim {suffix}: {shared_text}",
            )

        first = append_ledger_batch(
            conn,
            project,
            anchors["A"],
            [(anchors["A"], claims["A"])],
            document_id=str(document_a),
            channel_id=shared_cid,
            compile_version=version,
        )
        assert len(first) == 2  # anchor + claim block
        emit_event(
            conn,
            project,
            UNIT_COMMITTED,
            {
                "document_id": str(document_a),
                "channel_id": shared_cid,
                "compile_version": version,
                "ledger_block_ids": [str(block) for block in first],
            },
        )

        assert not batch_already_committed(
            conn,
            project,
            anchors["B"],
            [(anchors["B"], claims["B"])],
            document_id=str(document_b),
            channel_id=shared_cid,
            compile_version=version,
        )
        second = append_ledger_batch(
            conn,
            project,
            anchors["B"],
            [(anchors["B"], claims["B"])],
            document_id=str(document_b),
            channel_id=shared_cid,
            compile_version=version,
        )
        assert len(second) == 2  # second anchor + second claim block
        emit_event(
            conn,
            project,
            UNIT_COMMITTED,
            {
                "document_id": str(document_b),
                "channel_id": shared_cid,
                "compile_version": version,
                "ledger_block_ids": [str(block) for block in second],
            },
        )

        counts = {
            row["block_type"]: row["n"]
            for row in conn.execute(
                "SELECT block_type, count(*) AS n FROM ledger_blocks "
                "WHERE project_id = %s GROUP BY block_type",
                (str(project),),
            ).fetchall()
        }
        assert counts["derived"] == 2
        assert counts["claim"] == 2
        events = conn.execute(
            "SELECT count(*) AS n FROM events WHERE project_id = %s AND kind = %s "
            "AND data->>'channel_id' = %s AND data->>'compile_version' = %s",
            (str(project), UNIT_COMMITTED, shared_cid, version),
        ).fetchone()["n"]
        assert events == 2
        report = verify_ledger(conn, project)
        assert report.ok, report.findings
    finally:
        conn.close()


def test_marker_without_blocks_does_not_suppress_a_commit(test_dsn: str) -> None:
    """HF2: a UNIT_COMMITTED marker must carry a batch to count as proof."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        document = seed_document(conn, project)
        channel = "marker-without-blocks-channel"
        citation = seed_node(
            conn, project, f"source:no-blocks-{uuid4().hex[:8]}", node_type="citation"
        )
        seed_channel(conn, project, document, channel_id=channel)
        claim = seed_claim(conn, project, citation, channel, document)

        emit_event(
            conn,
            project,
            UNIT_COMMITTED,
            {
                "document_id": str(document),
                "channel_id": channel,
                "compile_version": "v1",
                "ledger_block_ids": [],
            },
        )
        assert not batch_already_committed(
            conn,
            project,
            citation,
            [(citation, claim)],
            document_id=str(document),
            channel_id=channel,
            compile_version="v1",
        )
        ids = append_ledger_batch(
            conn,
            project,
            citation,
            [(citation, claim)],
            document_id=str(document),
            channel_id=channel,
            compile_version="v1",
        )
        assert len(ids) == 2
        assert verify_ledger(conn, project).ok
    finally:
        conn.close()


def test_deleting_all_blocks_is_a_self_consistent_broken_report(test_dsn: str) -> None:
    """HF3: findings, rows and broken_chains agree when every block is gone."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        anchor = seed_node(conn, project, "source:all-deleted")
        claim = _real_claims(conn, project, 1)[0]
        blocks = append_ledger_batch(conn, project, anchor, [(anchor, claim)])
        emit_event(
            conn,
            project,
            UNIT_COMMITTED,
            {"ledger_block_ids": [str(block) for block in blocks]},
        )
        assert verify_ledger(conn, project).ok

        conn.execute("DELETE FROM ledger_blocks WHERE project_id = %s", (str(project),))
        report = verify_ledger(conn, project)
        assert report.ok is False
        assert report.exit_code == 1
        assert report.counts["broken_chains"] >= 1
        assert any("tail truncation" in finding for finding in report.findings)
        rows = [row for row in report.rows if row["project_id"] == str(project)]
        assert len(rows) == 1
        assert rows[0]["status"] == "broken"
        assert report.to_dict()["ok"] is False
    finally:
        conn.close()


def test_standalone_tail_without_event_is_a_warning(test_dsn: str) -> None:
    """G10: no event for the terminal block warns but does not break the chain."""

    conn = connect_db(test_dsn)
    try:
        project = seed_project(conn)
        anchor = seed_node(conn, project, "source:standalone-tail")
        claim = _real_claims(conn, project, 1)[0]
        committed = append_ledger_batch(conn, project, anchor, [(anchor, claim)])
        emit_event(
            conn,
            project,
            UNIT_COMMITTED,
            {"ledger_block_ids": [str(block) for block in committed]},
        )
        tail = append_ledger_batch_locked(project, anchor, [], dsn=test_dsn)
        assert len(tail) == 1
        report = verify_ledger(conn, project)
        assert report.ok
        assert report.counts["event_references.terminal_without_event"] == 1
        assert any("terminal block" in w and "no commit event" in w for w in report.warnings)
    finally:
        conn.close()
