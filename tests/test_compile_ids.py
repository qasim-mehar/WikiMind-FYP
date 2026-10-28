"""Deterministic ID derivation (L1 pattern) — PYTHONHASHSEED-independent."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from uuid import UUID

import pytest

from wikimind.compile.ids import (
    citation_node_slug,
    claim_id,
    edge_id,
    node_id,
    normalize_claim_text,
    slugify_entity,
)

PROJECT = UUID("00000000-0000-0000-0000-000000000001")
DOCUMENT = UUID("00000000-0000-0000-0000-000000000002")
CHANNEL = "a" * 64
NODE_A = UUID("00000000-0000-0000-0000-00000000000a")
NODE_B = UUID("00000000-0000-0000-0000-00000000000b")

_DERIVATION_SNIPPET = """
import json
from uuid import UUID
from wikimind.compile.ids import claim_id, node_id, edge_id
ids = {
    "claim": str(claim_id(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        document_id=UUID("00000000-0000-0000-0000-000000000002"),
        channel_id="a" * 64,
        claim_text="Alpha  joined\\u00a0Beta Labs.",
    )),
    "node": str(node_id(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        slug="alpha",
    )),
    "edge": str(edge_id(
        project_id=UUID("00000000-0000-0000-0000-000000000001"),
        src_node=UUID("00000000-0000-0000-0000-00000000000a"),
        dst_node=UUID("00000000-0000-0000-0000-00000000000b"),
        edge_type="contradicts",
    )),
}
print(json.dumps(ids))
"""


@pytest.mark.parametrize("seed", ["0", "1", "12345", "random"])
def test_ids_are_pythonhashseed_independent(seed: str) -> None:
    results = []
    for _ in range(2):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        proc = subprocess.run(
            [sys.executable, "-c", _DERIVATION_SNIPPET],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        results.append(ast.literal_eval(proc.stdout.strip()))
    assert results[0] == results[1]


def test_claim_id_inputs_change_the_id() -> None:
    base = claim_id(
        project_id=PROJECT, document_id=DOCUMENT, channel_id=CHANNEL, claim_text="Alpha is large."
    )
    same = claim_id(
        project_id=PROJECT, document_id=DOCUMENT, channel_id=CHANNEL, claim_text="Alpha  is large."
    )
    other_text = claim_id(
        project_id=PROJECT, document_id=DOCUMENT, channel_id=CHANNEL, claim_text="Alpha is small."
    )
    other_channel = claim_id(
        project_id=PROJECT, document_id=DOCUMENT, channel_id="b" * 64, claim_text="Alpha is large."
    )
    other_project = claim_id(
        project_id=UUID("00000000-0000-0000-0000-000000000009"),
        document_id=DOCUMENT,
        channel_id=CHANNEL,
        claim_text="Alpha is large.",
    )
    assert same == base  # whitespace normalization
    assert len({base, other_text, other_channel, other_project}) == 4


def test_node_and_edge_ids_are_deterministic() -> None:
    assert node_id(project_id=PROJECT, slug="alpha") == node_id(project_id=PROJECT, slug="alpha")
    assert node_id(project_id=PROJECT, slug="alpha") != node_id(project_id=PROJECT, slug="beta")
    assert edge_id(
        project_id=PROJECT, src_node=NODE_A, dst_node=NODE_B, edge_type="supersedes"
    ) == edge_id(project_id=PROJECT, src_node=NODE_A, dst_node=NODE_B, edge_type="supersedes")
    assert edge_id(
        project_id=PROJECT, src_node=NODE_A, dst_node=NODE_B, edge_type="supersedes"
    ) != edge_id(project_id=PROJECT, src_node=NODE_A, dst_node=NODE_B, edge_type="contradicts")
    # Direction matters.
    assert edge_id(
        project_id=PROJECT, src_node=NODE_A, dst_node=NODE_B, edge_type="supersedes"
    ) != edge_id(project_id=PROJECT, src_node=NODE_B, dst_node=NODE_A, edge_type="supersedes")


def test_slugify_entity() -> None:
    assert slugify_entity("Self-Attention (Transformer)") == "self-attention-transformer"
    assert slugify_entity("  GPT-4  ") == "gpt-4"
    assert slugify_entity("Beta   Labs") == "beta-labs"
    assert slugify_entity("---") == ""
    assert slugify_entity("  ") == ""


def test_normalize_claim_text_and_citation_slug() -> None:
    assert normalize_claim_text("Alpha  joined\u00a0Beta.") == "Alpha joined Beta."
    assert citation_node_slug("deadbeef") == "source:deadbeef"
