"""Deterministic content-derived identifiers for the compile lane.

The derivation follows Lane 1's documented pattern (``wikimind.parse.ids``):
a lowercase SHA-256 hex digest over a UTF-8 payload string whose fields are
joined with the ASCII unit separator.  ``hashlib`` is independent of
``PYTHONHASHSEED`` and of process randomness, so the same inputs yield the
same UUIDs on every run and platform.

Claims, nodes, and edges are primary-key UUIDs, so the 64-character digest
is folded to a UUID by taking its first 32 hex characters.  ``project_id``
is part of every payload, which keeps IDs globally unique across projects.

The claim payload includes ``COMPILE_PROMPT_VERSION``: a future governance
change to the prompts or pins bumps that constant, which changes every
derived claim ID and cleanly invalidates unit-level short-circuits.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from uuid import UUID

from .prompts import COMPILE_PROMPT_VERSION

SEPARATOR = "\x1f"

_SLUG_BAD_CHARS = re.compile(r"[^a-z0-9]+")


def _uuid_from_payload(payload: str) -> UUID:
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return UUID(digest[:32])


def slugify_entity(name: str) -> str:
    """Return the normalized node slug for a proposed entity name.

    This is the "normalized entity-name matching" used by the WRITE
    neighborhood fetch and by node reuse: lowercase, Unicode-whitespace and
    punctuation folded to single dashes.  An empty result means the name
    carries no slug-able characters and the caller should skip it.
    """

    normalized = unicodedata.normalize("NFKC", name).strip().lower()
    slug = _SLUG_BAD_CHARS.sub("-", normalized).strip("-")
    return slug


def normalize_claim_text(text: str) -> str:
    """Normalize claim text for the dedup pre-filter (L1 normalization)."""

    return " ".join(unicodedata.normalize("NFC", text).split())


def citation_node_slug(document_sha256: str) -> str:
    """The stable citation-page slug for a document (Level 1, per D2)."""

    return f"source:{document_sha256}"


def claim_id(
    *,
    project_id: UUID,
    document_id: UUID,
    channel_id: str,
    claim_text: str,
    compile_version: str = COMPILE_PROMPT_VERSION,
) -> UUID:
    """Deterministic claim identity from its provenance and final text."""

    payload = SEPARATOR.join(
        [
            "claim",
            str(project_id),
            str(document_id),
            channel_id,
            compile_version,
            normalize_claim_text(claim_text),
        ]
    )
    return _uuid_from_payload(payload)


def node_id(*, project_id: UUID, slug: str) -> UUID:
    """Deterministic node identity from the (project, slug) key."""

    return _uuid_from_payload(SEPARATOR.join(["node", str(project_id), slug]))


def edge_id(*, project_id: UUID, src_node: UUID, dst_node: UUID, edge_type: str) -> UUID:
    """Deterministic edge identity from the (project, src, dst, type) key."""

    return _uuid_from_payload(
        SEPARATOR.join(["edge", str(project_id), str(src_node), str(dst_node), edge_type])
    )
