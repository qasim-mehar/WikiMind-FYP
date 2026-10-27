"""Index lane: embedding store, incremental indexing, retrieval substrates.

Substrate only: QA-facing ranking/fusion policy and user-facing surfaces are
post-Gate-B scope.
"""

from __future__ import annotations

from .indexer import (
    DEFAULT_BATCH_SIZE,
    INDEX_CALL_SITE,
    IndexResult,
    ProjectRef,
    embedder_model_label,
    index_project,
    resolve_project,
)
from .retrieve import (
    AnnHit,
    FtsHit,
    GraphHit,
    HybridHits,
    ann_search,
    fulltext_search,
    hybrid_fetch,
    k_hop_expansion,
)
from .store import format_vector, insert_embeddings, stored_version_ids

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "INDEX_CALL_SITE",
    "AnnHit",
    "FtsHit",
    "GraphHit",
    "HybridHits",
    "IndexResult",
    "ProjectRef",
    "ann_search",
    "embedder_model_label",
    "format_vector",
    "fulltext_search",
    "hybrid_fetch",
    "index_project",
    "insert_embeddings",
    "k_hop_expansion",
    "resolve_project",
    "stored_version_ids",
]
