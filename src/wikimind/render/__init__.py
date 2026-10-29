"""Node-version materialization (Governance Ruling 14).

``rendered_text`` of a node version is a pure deterministic function of
stored rows: the node's slug and type plus the texts of the claims connected
to it.  Rendering is local — no LLM calls anywhere in this package.
"""

from .versions import (
    BackfillResult,
    backfill_project,
    materialize_version,
    node_claim_texts,
    render_node,
    render_node_text,
)

__all__ = [
    "BackfillResult",
    "backfill_project",
    "materialize_version",
    "node_claim_texts",
    "render_node",
    "render_node_text",
]
