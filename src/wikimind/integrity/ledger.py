"""V2c/V6: hash-chained claim ledger writer and verifier.

Chaining scheme (the frozen ``ledger_blocks`` table has no hash column, so the
UUID primary key is the hash slot):

* One chain per project, ordered by the global ``position`` counter
  (0, 1, 2, ... contiguous).  The per-project writer lock makes the counter
  safe; a project's blocks therefore form one end-to-end chain.
* Each commit batch appends exactly one ``derived`` block -- the batch anchor
  (``claim_id IS NULL``) -- followed by one ``claim`` block per claim the batch
  commits, each attached to the L2 node it belongs to.  The anchor's node is
  the unit's citation node.  All-rejected units still produce an anchor-only
  batch: that is intentional, the unit marker must have a ledger record.
* A block's ``id`` is a UUIDv8-shaped value computed by
  :func:`wikimind.integrity.ids.ledger_block_uuid` over the domain separator,
  ``project_id``, the *previous block's id*, ``position``, ``node_id``,
  ``claim_id``, ``block_type`` and a payload digest.  For claim blocks the
  payload digest is empty; for an anchor it is the fingerprint of the ordered
  ``(node_id, claim_id)`` pairs of the claim blocks that follow it in the
  batch.  Genesis uses the all-zero UUID as the predecessor.

Verification recomputes every id from the previous stored id and the row's
own columns (recomputing each anchor's batch fingerprint from the claim blocks
that follow it), checks that positions are contiguous, and cross-checks the
``ledger_block_ids`` recorded in ``UNIT_COMMITTED`` events against the chain.
A truncation of the chain's tail is detected because the events outlive it; a
terminal block with no event is only a warning, never a broken chain.

**Honest scope:** the chain is tamper-evident against tampering with the
``ledger_blocks`` table alone (row changes, deletions, reordering, batch
membership changes, tail truncation).  It is NOT resistant to an attacker who
can rewrite both ``ledger_blocks`` and the ``events`` table consistently.
Writers only INSERT; nothing here ever updates or deletes a block.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from wikimind.db.connection import connect
from wikimind.db.locks import with_writer_lock

from .events import UNIT_COMMITTED
from .ids import ZERO_UUID, batch_fingerprint, ledger_block_uuid
from .model import IntegrityReport

INSERT_BLOCK = """
    INSERT INTO ledger_blocks
        (id, project_id, node_id, claim_id, position, block_type)
    VALUES (%s, %s, %s, %s, %s, %s)
"""


@dataclass(frozen=True, slots=True)
class LedgerBlock:
    """One stored block, normalized to strings for stable hashing."""

    id: str
    project_id: str
    node_id: str
    claim_id: str | None
    position: int
    block_type: str

    @classmethod
    def from_row(cls, row: object) -> LedgerBlock:
        return cls(
            id=str(row["id"]).lower(),  # type: ignore[index]
            project_id=str(row["project_id"]).lower(),  # type: ignore[index]
            node_id=str(row["node_id"]).lower(),  # type: ignore[index]
            claim_id=None if row["claim_id"] is None else str(row["claim_id"]).lower(),  # type: ignore[index]
            position=int(row["position"]),  # type: ignore[index]
            block_type=str(row["block_type"]),  # type: ignore[index]
        )


def _candidate_pairs(entries: Sequence[tuple[UUID | str, UUID | str]]) -> list[tuple[str, str]]:
    """Dedupe entries to one block per (node, claim), in first-seen order.

    Several accepted claims can share a deterministic claim id; the frozen
    ``UNIQUE(node_id, claim_id)`` constraint must not be violated.
    """

    return list(dict.fromkeys((str(node).lower(), str(claim).lower()) for node, claim in entries))


def _existing_pairs(
    conn: psycopg.Connection, project_id: str, candidates: Sequence[tuple[str, str]]
) -> set[tuple[str, str]]:
    if not candidates:
        return set()
    node_ids = sorted({node for node, _ in candidates})
    rows = conn.execute(
        "SELECT node_id, claim_id FROM ledger_blocks "
        "WHERE project_id = %s AND node_id = ANY(%s) AND claim_id IS NOT NULL",
        (project_id, node_ids),
    ).fetchall()
    return {(str(r["node_id"]).lower(), str(r["claim_id"]).lower()) for r in rows}


def _next_position(conn: psycopg.Connection, project_id: str) -> tuple[int, str]:
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_position "
        "FROM ledger_blocks WHERE project_id = %s",
        (project_id,),
    ).fetchone()
    previous = conn.execute(
        "SELECT id FROM ledger_blocks WHERE project_id = %s ORDER BY position DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    position = int(row["next_position"])
    prev_id = ZERO_UUID if previous is None else str(previous["id"]).lower()
    return position, prev_id


def anchor_id_for_batch(
    conn: psycopg.Connection,
    project_id: UUID | str,
    anchor_node_id: UUID | str,
    entries: Sequence[tuple[UUID | str, UUID | str]],
) -> UUID:
    """The deterministic anchor id the writer would produce at this moment."""

    pid = str(project_id)
    candidate = _candidate_pairs(entries)
    fresh = [pair for pair in candidate if pair not in _existing_pairs(conn, pid, candidate)]
    position, prev_id = _next_position(conn, pid)
    return ledger_block_uuid(
        project_id=pid,
        prev_id=prev_id,
        position=position,
        node_id=str(anchor_node_id).lower(),
        claim_id=None,
        block_type="derived",
        payload_digest=batch_fingerprint(pid, str(anchor_node_id).lower(), fresh),
    )


def _unit_marker_exists(
    conn: psycopg.Connection,
    project_id: str,
    document_id: str | None,
    channel_id: str | None,
    compile_version: str | None,
) -> bool:
    """Stable retry marker carried by UNIT_COMMITTED event metadata (G9).

    ``ledger_blocks`` has no column for a unit identity, and an empty batch
    has no claim pairs to recognize later.  The event row survives chain
    advancement, so the full compile unit identity -- (project, document,
    channel, compile_version) -- is the marker.  ``document_id`` is essential:
    channel ids are content hashes that are NOT unique across documents
    (migration 002), so omitting it would suppress a legitimate batch in a
    second document that shares a channel (quality-pass H1).

    HF2: the event must also carry a non-empty ``ledger_block_ids`` array --
    a marker without a batch proves nothing and must not suppress a commit.
    """

    if document_id is None or channel_id is None or compile_version is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM events WHERE project_id = %s AND kind = %s "
        "AND data->>'document_id' = %s AND data->>'channel_id' = %s "
        "AND data->>'compile_version' = %s "
        "AND COALESCE(jsonb_array_length(data->'ledger_block_ids'), 0) > 0 LIMIT 1",
        (
            project_id,
            UNIT_COMMITTED,
            str(document_id),
            str(channel_id),
            str(compile_version),
        ),
    ).fetchone()
    return row is not None


def batch_already_committed(
    conn: psycopg.Connection,
    project_id: UUID | str,
    anchor_node_id: UUID | str,
    entries: Sequence[tuple[UUID | str, UUID | str]],
    *,
    document_id: str | None = None,
    channel_id: str | None = None,
    compile_version: str | None = None,
) -> bool:
    """Whether this batch was already appended (retry-safe short-circuit).

    Three cases mean "already done": a UNIT_COMMITTED marker for the full unit
    (this is what makes empty batches idempotent after the chain advanced),
    every candidate pair already in the chain, or the deterministic anchor id
    for the current chain state already exists (a mid-batch failure retry).
    """

    pid = str(project_id)
    if _unit_marker_exists(conn, pid, document_id, channel_id, compile_version):
        return True
    candidate = _candidate_pairs(entries)
    seen = _existing_pairs(conn, pid, candidate)
    if candidate and all(pair in seen for pair in candidate):
        return True
    anchor_id = str(anchor_id_for_batch(conn, pid, anchor_node_id, entries))
    row = conn.execute(
        "SELECT 1 FROM ledger_blocks WHERE id = %s AND project_id = %s",
        (anchor_id, pid),
    ).fetchone()
    return row is not None


def append_ledger_batch(
    conn: psycopg.Connection,
    project_id: UUID | str,
    anchor_node_id: UUID | str,
    entries: Sequence[tuple[UUID | str, UUID | str]],
    *,
    document_id: str | None = None,
    channel_id: str | None = None,
    compile_version: str | None = None,
) -> list[UUID]:
    """Append one batch (anchor + claim blocks) on ``conn``.

    The caller MUST hold the canonical per-project writer lock (``commit_unit``
    does).  Returns the new block ids in chain order, or an empty list when the
    batch was already committed.  Claim pairs already present are skipped, so
    a retried commit cannot create a gap or break the chain.  The optional unit
    marker makes even an empty batch idempotent across chain advancement.
    """

    pid = str(project_id)
    if _unit_marker_exists(conn, pid, document_id, channel_id, compile_version):
        return []
    anchor = str(anchor_node_id).lower()
    candidate = _candidate_pairs(entries)
    seen = _existing_pairs(conn, pid, candidate)
    fresh = [pair for pair in candidate if pair not in seen]
    if candidate and not fresh:
        return []  # already committed; no phantom anchor

    position, prev_id = _next_position(conn, pid)
    fingerprint = batch_fingerprint(pid, anchor, fresh)
    anchor_id = ledger_block_uuid(
        project_id=pid,
        prev_id=prev_id,
        position=position,
        node_id=anchor,
        claim_id=None,
        block_type="derived",
        payload_digest=fingerprint,
    )
    conn.execute(INSERT_BLOCK, (str(anchor_id), pid, anchor, None, position, "derived"))
    ids = [anchor_id]

    for offset, (node_id, claim_id) in enumerate(fresh, start=1):
        block_id = ledger_block_uuid(
            project_id=pid,
            prev_id=str(ids[-1]),
            position=position + offset,
            node_id=node_id,
            claim_id=claim_id,
            block_type="claim",
        )
        conn.execute(
            INSERT_BLOCK,
            (str(block_id), pid, node_id, claim_id, position + offset, "claim"),
        )
        ids.append(block_id)
    return ids


def append_ledger_batch_locked(
    project_id: UUID | str,
    anchor_node_id: UUID | str,
    entries: Sequence[tuple[UUID | str, UUID | str]],
    *,
    dsn: str | None = None,
) -> list[UUID]:
    """Standalone wrapper that takes the per-project writer lock."""

    with (
        connect(dsn, row_factory=dict_row, autocommit=True) as conn,
        with_writer_lock(project_id, conn),
    ):
        return append_ledger_batch(conn, project_id, anchor_node_id, entries)


def _anchor_members(blocks: Sequence[LedgerBlock], index: int) -> list[tuple[str, str]]:
    """Claim blocks following the anchor at ``index``, up to the next anchor."""

    members: list[tuple[str, str]] = []
    for block in blocks[index + 1 :]:
        if block.block_type == "derived":
            break
        if block.block_type == "claim" and block.claim_id is not None:
            members.append((block.node_id, block.claim_id))
    return members


def verify_chain(blocks: Sequence[LedgerBlock]) -> IntegrityReport:
    """Verify one or more project chains.  Pure; no database access."""

    report = IntegrityReport("ledger")
    by_project: dict[str, list[LedgerBlock]] = {}
    for block in blocks:
        by_project.setdefault(block.project_id, []).append(block)

    report.set_count("projects", len(by_project))
    report.set_count("blocks", len(blocks))
    for project_id, chain in sorted(by_project.items()):
        chain = sorted(chain, key=lambda block: block.position)
        derived = sum(1 for block in chain if block.block_type == "derived")
        claims = sum(1 for block in chain if block.block_type == "claim")
        report.count("derived_blocks", derived)
        report.count("claim_blocks", claims)
        broken = False
        prev_id = ZERO_UUID
        for index, block in enumerate(chain):
            if block.position != index:
                report.finding(
                    f"project {project_id}: broken at block {index} "
                    f"(position {block.position}): non-contiguous position"
                )
                broken = True
                break
            digest = (
                batch_fingerprint(project_id, block.node_id, _anchor_members(chain, index))
                if block.block_type == "derived"
                else ""
            )
            expected = str(
                ledger_block_uuid(
                    project_id=project_id,
                    prev_id=prev_id,
                    position=block.position,
                    node_id=block.node_id,
                    claim_id=block.claim_id,
                    block_type=block.block_type,
                    payload_digest=digest,
                )
            )
            if expected != block.id:
                report.finding(
                    f"project {project_id}: broken at block {index} "
                    f"(position {block.position}, id {block.id}): hash mismatch"
                )
                broken = True
                break
            prev_id = block.id
        if broken:
            report.count("broken_chains", 1)
        else:
            report.count("valid_chains", 1)
        report.rows.append(
            {
                "project_id": project_id,
                "blocks": len(chain),
                "derived_blocks": derived,
                "claim_blocks": claims,
                "status": "broken" if broken else "valid",
            }
        )
    if not by_project:
        report.count("valid_chains", 0)
    return report


def _mark_broken(report: IntegrityReport, project_id: str) -> None:
    for row in report.rows:
        if row["project_id"] == project_id:
            if row["status"] == "valid":
                row["status"] = "broken"
                report.count("valid_chains", -1)
                report.count("broken_chains", 1)
            return
    # HF3: an event may reference blocks of a project whose chain rows are
    # entirely gone.  Keep the JSON report self-consistent by recording an
    # explicit broken row rather than leaving a finding with no broken chain.
    report.rows.append(
        {
            "project_id": project_id,
            "blocks": 0,
            "derived_blocks": 0,
            "claim_blocks": 0,
            "status": "broken",
            "note": "all blocks missing; events still reference them",
        }
    )
    report.count("projects", 1)
    report.count("broken_chains", 1)


def verify_event_references(
    conn: psycopg.Connection,
    blocks: Sequence[LedgerBlock],
    report: IntegrityReport,
    project_id: UUID | str | None = None,
) -> IntegrityReport:
    """Cross-check UNIT_COMMITTED block ids against the stored chain.

    Events outlive the chain on a tail-only tamper: the referenced ids are
    absent while the surviving positions are still contiguous.
    """

    known: dict[str, dict[str, int]] = {}
    terminal: dict[str, int] = {}
    terminal_ids: dict[str, set[str]] = {}
    for block in blocks:
        known.setdefault(block.project_id, {})[block.id] = block.position
        pid = block.project_id
        current = terminal.get(pid, -1)
        if block.position > current:
            terminal[pid] = block.position
            terminal_ids[pid] = {block.id}
        elif block.position == current:
            terminal_ids.setdefault(pid, set()).add(block.id)

    if project_id is None:
        rows = conn.execute(
            "SELECT project_id, data FROM events WHERE kind = %s ORDER BY id",
            (UNIT_COMMITTED,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT project_id, data FROM events WHERE kind = %s AND project_id = %s ORDER BY id",
            (UNIT_COMMITTED, str(project_id)),
        ).fetchall()

    referenced_ids: dict[str, set[str]] = {}
    report.set_count("event_references.events", len(rows))
    for row in rows:
        pid = str(row["project_id"]).lower()
        data = row["data"] or {}
        ids = data.get("ledger_block_ids") or []
        for block_id in ids:
            block_id = str(block_id).lower()
            report.count("event_references.referenced", 1)
            position = known.get(pid, {}).get(block_id)
            if position is None:
                # Only an event-referenced id missing from the chain proves
                # tail truncation; events outlive the chain.
                report.count("event_references.missing", 1)
                report.finding(
                    f"project {pid}: tail truncation at position {terminal.get(pid, -1)}: "
                    f"event-referenced ledger block {block_id} is missing"
                )
                _mark_broken(report, pid)
            else:
                referenced_ids.setdefault(pid, set()).add(block_id)

    # G10: a terminal block with no commit event is advisory, not broken.
    # Standalone appends and pre-event corpus blocks legitimately have none.
    for pid, ids_at_terminal in sorted(terminal_ids.items()):
        if not (ids_at_terminal & referenced_ids.get(pid, set())):
            report.warning(f"project {pid}: terminal block {terminal[pid]} has no commit event")
            report.count("event_references.terminal_without_event", 1)
    return report


def verify_ledger(
    conn: psycopg.Connection,
    project_id: UUID | str | None = None,
) -> IntegrityReport:
    """Load stored blocks and verify their chains end to end."""

    if project_id is None:
        rows = conn.execute(
            "SELECT id, project_id, node_id, claim_id, position, block_type "
            "FROM ledger_blocks ORDER BY project_id, position"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, project_id, node_id, claim_id, position, block_type "
            "FROM ledger_blocks WHERE project_id = %s ORDER BY position",
            (str(project_id),),
        ).fetchall()
    blocks = [LedgerBlock.from_row(row) for row in rows]
    report = verify_chain(blocks)
    return verify_event_references(conn, blocks, report, project_id)
