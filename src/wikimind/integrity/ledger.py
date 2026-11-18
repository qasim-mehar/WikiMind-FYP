"""V2c/V6: hash-chained claim ledger writer and verifier.

Chaining scheme (the frozen ``ledger_blocks`` table has no hash column, so the
UUID primary key is the hash slot):

* One chain per project, ordered by the global ``position`` counter
  (0, 1, 2, ... contiguous).  The per-project writer lock makes the counter
  safe; a project's blocks therefore form one end-to-end chain.
* Each commit batch appends exactly one ``derived`` block -- the batch anchor
  (``claim_id IS NULL``) -- followed by one ``claim`` block per claim the batch
  commits, each attached to the L2 node it belongs to.  The anchor's node is
  the unit's citation node.
* A block's ``id`` is a UUIDv8-shaped value computed by
  :func:`wikimind.integrity.ids.ledger_block_uuid` over the domain separator,
  ``project_id``, the *previous block's id*, ``position``, ``node_id``,
  ``claim_id``, ``block_type`` and a payload digest.  For claim blocks the
  payload digest is empty; for an anchor it is the SHA-256 of the ordered
  ``(node_id, claim_id)`` pairs of the claim blocks that follow it in the
  batch.  Genesis uses the all-zero UUID as the predecessor.

Verification recomputes every id from the previous stored id and the row's
own columns (recomputing each anchor's batch digest from the claim blocks that
follow it), and checks that positions are contiguous.  Any change to a row, a
deletion, a reordering, or a change in batch membership breaks the chain.
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

from .ids import ZERO_UUID, batch_digest, ledger_block_uuid
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


def append_ledger_batch(
    conn: psycopg.Connection,
    project_id: UUID | str,
    anchor_node_id: UUID | str,
    entries: Sequence[tuple[UUID | str, UUID | str]],
) -> list[UUID]:
    """Append one batch (anchor + claim blocks) on ``conn``.

    The caller MUST hold the canonical per-project writer lock (``commit_unit``
    does).  Returns the new block ids in chain order.  Entries already present
    for a ``(node_id, claim_id)`` pair are skipped, so a retried commit cannot
    create a gap or break the chain.
    """

    pid = str(project_id)
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS next_position "
        "FROM ledger_blocks WHERE project_id = %s",
        (pid,),
    ).fetchone()
    position = int(row["next_position"])
    previous = conn.execute(
        "SELECT id FROM ledger_blocks WHERE project_id = %s ORDER BY position DESC LIMIT 1",
        (pid,),
    ).fetchone()
    prev_id = ZERO_UUID if previous is None else str(previous["id"]).lower()

    # Several accepted claims can share a deterministic claim id (the
    # compile-id test does exactly that).  Keep one block per (node, claim)
    # pair, in first-seen order, so the batch cannot violate the frozen
    # UNIQUE(node_id, claim_id) constraint.
    candidate = list(
        dict.fromkeys((str(node).lower(), str(claim).lower()) for node, claim in entries)
    )
    if candidate:
        node_ids = sorted({node for node, _ in candidate})
        existing_rows = conn.execute(
            "SELECT node_id, claim_id FROM ledger_blocks "
            "WHERE node_id = ANY(%s) AND claim_id IS NOT NULL",
            (node_ids,),
        ).fetchall()
        seen = {(str(r["node_id"]).lower(), str(r["claim_id"]).lower()) for r in existing_rows}
        fresh = [pair for pair in candidate if pair not in seen]
    else:
        fresh = []

    anchor_id = ledger_block_uuid(
        project_id=pid,
        prev_id=prev_id,
        position=position,
        node_id=str(anchor_node_id).lower(),
        claim_id=None,
        block_type="derived",
        payload_digest=batch_digest(fresh),
    )
    conn.execute(
        INSERT_BLOCK,
        (str(anchor_id), pid, str(anchor_node_id).lower(), None, position, "derived"),
    )
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
                batch_digest(_anchor_members(chain, index)) if block.block_type == "derived" else ""
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
    return verify_chain([LedgerBlock.from_row(row) for row in rows])
