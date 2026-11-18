"""Deterministic UUIDs used as content addresses in the frozen schema.

Both the cost mirror and the ledger chain need a stable identity that is *not*
``gen_random_uuid()``: the ledger uses ``ledger_blocks.id`` as its hash slot
(there is no hash column in the frozen table), and both writers use a
deterministic UUID as their idempotency key (``ON CONFLICT (id) DO NOTHING``).

The scheme is UUIDv8-shaped: the first 16 bytes of a domain-separated SHA-256
are stored with the version and variant bits set, so the value remains a valid
UUID while committing to every input byte.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from uuid import UUID

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
LEDGER_DOMAIN = "wikimind-ledger-v1"
COST_DOMAIN = "wikimind-cost-v1"
SEPARATOR = "\x1f"


def _uuid_from_digest(digest: bytes) -> UUID:
    octets = bytearray(digest[:16])
    octets[6] = (octets[6] & 0x0F) | 0x80  # UUID version 8
    octets[8] = (octets[8] & 0x3F) | 0x80  # RFC 4122 variant
    return UUID(bytes=bytes(octets))


def deterministic_uuid(domain: str, *parts: str) -> UUID:
    """Hash ``domain`` and ``parts`` into a stable UUIDv8-shaped value."""

    # Length-prefixing makes the encoding injective: without it, ("a\x1fb",)
    # and ("a", "b") would hash to the same byte string.
    canonical = SEPARATOR.join(f"{len(part)}:{part}" for part in (domain, *parts))
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return _uuid_from_digest(digest)


def cost_event_uuid(canonical_json: str) -> UUID:
    """Identity of one cost ledger line (the exact JSON emitted to JSONL)."""

    return deterministic_uuid(COST_DOMAIN, canonical_json)


def ledger_block_uuid(
    *,
    project_id: str | UUID,
    prev_id: str | UUID,
    position: int,
    node_id: str | UUID,
    claim_id: str | UUID | None,
    block_type: str,
    payload_digest: str = "",
) -> UUID:
    """Identity of one ledger block, chained to the previous block's id."""

    return deterministic_uuid(
        LEDGER_DOMAIN,
        str(project_id),
        str(prev_id),
        str(position),
        str(node_id),
        "" if claim_id is None else str(claim_id),
        block_type,
        payload_digest,
    )


def batch_digest(entries: Sequence[tuple[str, str]]) -> str:
    """Hex SHA-256 of the ordered ``(node_id, claim_id)`` pairs in a batch.

    This is stored implicitly: it is hashed into the batch anchor block's id,
    which is the only hash slot the frozen ``ledger_blocks`` table provides.
    """

    canonical = SEPARATOR.join(f"{node_id}:{claim_id}" for node_id, claim_id in entries)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
