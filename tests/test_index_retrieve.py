"""Retrieval substrates: ANN ordering, k-hop expansion, full-text, hybrid union."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from index_seed import (
    basis_vector,
    seed_edge,
    seed_embedding,
    seed_node,
    seed_node_version,
    seed_project,
)

from wikimind.index import (
    ann_search,
    format_vector,
    fulltext_search,
    hybrid_fetch,
    k_hop_expansion,
)


class Model:
    """Sentinel model label so test rows never collide with real model names."""

    A = "test-model-a"
    B = "test-model-b"


@pytest.fixture
def index_conn(test_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        yield conn


def _version_with_vector(conn, project_id, slug, text, model):
    node_id = seed_node(conn, project_id, slug)
    version_id = seed_node_version(conn, project_id, node_id, text)
    seed_embedding(conn, project_id, version_id, model, basis_vector(0))
    return node_id, version_id


def test_format_vector_literal_and_guards() -> None:
    assert format_vector([0.5, -1.5]) == "[0.5, -1.5]"
    assert format_vector([1e-05]) == "[1e-05]"
    with pytest.raises(ValueError, match="finite"):
        format_vector([float("nan")])
    with pytest.raises(ValueError, match="finite"):
        format_vector([float("inf")])
    with pytest.raises(ValueError, match="empty"):
        format_vector([])


def test_ann_search_orders_by_cosine_similarity(index_conn) -> None:
    project_id = seed_project(index_conn, uuid4().hex)
    node_same = seed_node(index_conn, project_id, "same")
    node_ortho = seed_node(index_conn, project_id, "ortho")
    node_opposed = seed_node(index_conn, project_id, "opposed")
    version_same = seed_node_version(index_conn, project_id, node_same, "same direction")
    version_ortho = seed_node_version(index_conn, project_id, node_ortho, "orthogonal")
    version_opposed = seed_node_version(index_conn, project_id, node_opposed, "opposed")
    seed_embedding(index_conn, project_id, version_same, Model.A, basis_vector(0))
    seed_embedding(index_conn, project_id, version_ortho, Model.A, basis_vector(1))
    seed_embedding(
        index_conn,
        project_id,
        version_opposed,
        Model.A,
        [-value for value in basis_vector(0)],
    )

    hits = ann_search(index_conn, project_id, basis_vector(0), k=3, model=Model.A)

    assert [hit.node_version_id for hit in hits] == [
        version_same,
        version_ortho,
        version_opposed,
    ]
    assert [round(hit.similarity, 6) for hit in hits] == [1.0, 0.0, -1.0]
    assert [hit.node_id for hit in hits] == [node_same, node_ortho, node_opposed]


def test_ann_search_limits_filters_model_and_project(index_conn) -> None:
    project_a = seed_project(index_conn, uuid4().hex)
    project_b = seed_project(index_conn, uuid4().hex)
    node_a = seed_node(index_conn, project_a, "alpha")
    version_a = seed_node_version(index_conn, project_a, node_a, "a text")
    version_ortho = seed_node_version(
        index_conn, project_a, seed_node(index_conn, project_a, "beta"), "a orthogonal"
    )
    seed_embedding(index_conn, project_a, version_a, Model.A, basis_vector(0))
    seed_embedding(index_conn, project_a, version_ortho, Model.A, basis_vector(1))
    seed_embedding(index_conn, project_a, version_a, Model.B, basis_vector(0))
    _version_with_vector(index_conn, project_b, "alpha", "b text", Model.A)

    hits = ann_search(index_conn, project_a, basis_vector(0), k=10, model=Model.A)
    assert [hit.node_version_id for hit in hits] == [version_a, version_ortho]

    limited = ann_search(index_conn, project_a, basis_vector(0), k=1, model=Model.A)
    assert [hit.node_version_id for hit in limited] == [version_a]

    other_model = ann_search(index_conn, project_a, basis_vector(0), k=10, model=Model.B)
    assert [hit.node_version_id for hit in other_model] == [version_a]

    with pytest.raises(ValueError, match="k must be positive"):
        ann_search(index_conn, project_a, basis_vector(0), k=0, model=Model.A)


def test_ann_search_rejects_non_finite_query(index_conn) -> None:
    project_id = seed_project(index_conn, uuid4().hex)
    query = basis_vector(0)
    query[0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        ann_search(index_conn, project_id, query, model=Model.A)


def test_retrieval_surfaces_only_current_versions(index_conn) -> None:
    """H1: a node with v1 and v2 — every version-bearing path returns only v2.

    The superseded v1 keeps its embedding row (append-only store), but no
    retrieval path may surface it.
    """

    project_id = seed_project(index_conn, uuid4().hex)
    node_id = seed_node(index_conn, project_id, "alpha")
    version_old = seed_node_version(
        index_conn,
        project_id,
        node_id,
        "the superseded ferret history",
        version=1,
        set_current=False,
    )
    version_new = seed_node_version(
        index_conn, project_id, node_id, "the current ferret page", version=2
    )
    seed_embedding(index_conn, project_id, version_old, Model.A, basis_vector(0))
    seed_embedding(index_conn, project_id, version_new, Model.A, basis_vector(0))

    ann = ann_search(index_conn, project_id, basis_vector(0), k=10, model=Model.A)
    assert [hit.node_version_id for hit in ann] == [version_new]

    fts = fulltext_search(index_conn, project_id, "ferret")
    assert [hit.node_version_id for hit in fts] == [version_new]

    hybrid = hybrid_fetch(
        index_conn,
        project_id,
        query_vector=basis_vector(0),
        query_text="ferret",
        model=Model.A,
    )
    assert [hit.node_version_id for hit in hybrid.ann] == [version_new]
    assert [hit.node_version_id for hit in hybrid.fts] == [version_new]
    # The node itself still surfaces (at its current version).
    assert hybrid.node_ids == [node_id]


def test_k_hop_expansion_depths_and_determinism(index_conn) -> None:
    project_id = seed_project(index_conn, uuid4().hex)
    node_a = seed_node(index_conn, project_id, "a")
    node_b = seed_node(index_conn, project_id, "b")
    node_c = seed_node(index_conn, project_id, "c")
    node_d = seed_node(index_conn, project_id, "d")
    seed_edge(index_conn, project_id, node_a, node_b)
    seed_edge(index_conn, project_id, node_b, node_c)
    seed_edge(index_conn, project_id, node_c, node_d)
    # Expansion is undirected, so this backwards cycle edge makes node_c a
    # depth-1 neighbour of node_a while still exercising cycle termination.
    seed_edge(index_conn, project_id, node_c, node_a)

    zero = k_hop_expansion(index_conn, project_id, [node_a], hops=0)
    one = k_hop_expansion(index_conn, project_id, [node_a], hops=1)
    two = k_hop_expansion(index_conn, project_id, [node_a], hops=2)

    assert [(hit.node_id, hit.depth) for hit in zero] == [(node_a, 0)]
    assert {hit.node_id: hit.depth for hit in one} == {
        node_a: 0,
        node_b: 1,
        node_c: 1,
    }
    assert {hit.node_id: hit.depth for hit in two} == {
        node_a: 0,
        node_b: 1,
        node_c: 1,
        node_d: 2,
    }
    # Deterministic contract: ordered by (depth, node id).
    assert two == sorted(two, key=lambda hit: (hit.depth, hit.node_id))
    assert {hit.slug for hit in two} == {"a", "b", "c", "d"}
    assert all(hit.node_type == "concept" and hit.status == "active" for hit in two)


def test_k_hop_expansion_does_not_leak_across_projects(index_conn) -> None:
    project_a = seed_project(index_conn, uuid4().hex)
    project_b = seed_project(index_conn, uuid4().hex)
    node_a = seed_node(index_conn, project_a, "a")
    node_z = seed_node(index_conn, project_b, "z")
    # A foreign-project edge row touching project A nodes, and a project A
    # edge row pointing at a foreign node: neither may leak into the walk.
    seed_edge(index_conn, project_b, node_a, node_z)
    seed_edge(index_conn, project_a, node_a, node_z)

    hits = k_hop_expansion(index_conn, project_a, [node_a], hops=3)

    assert [hit.node_id for hit in hits] == [node_a]


def test_k_hop_expansion_follows_edges_in_both_directions(index_conn) -> None:
    project_id = seed_project(index_conn, uuid4().hex)
    node_a = seed_node(index_conn, project_id, "a")
    node_b = seed_node(index_conn, project_id, "b")
    seed_edge(index_conn, project_id, node_b, node_a)  # only b -> a exists

    hits = k_hop_expansion(index_conn, project_id, [node_a], hops=1)

    assert {hit.node_id for hit in hits} == {node_a, node_b}

    with pytest.raises(ValueError, match="hops must not be negative"):
        k_hop_expansion(index_conn, project_id, [node_a], hops=-1)
    assert k_hop_expansion(index_conn, project_id, []) == []


def test_fulltext_search_matches_stems_and_respects_limit(index_conn) -> None:
    project_id = seed_project(index_conn, uuid4().hex)
    node_capacitor = seed_node(index_conn, project_id, "capacitor-page")
    node_battery = seed_node(index_conn, project_id, "battery-page")
    version_capacitor = seed_node_version(
        index_conn, project_id, node_capacitor, "The capacitor shield rating is high"
    )
    version_battery = seed_node_version(
        index_conn, project_id, node_battery, "Battery lifetime report", version=1
    )

    hits = fulltext_search(index_conn, project_id, "capacitor")
    assert [hit.node_version_id for hit in hits] == [version_capacitor]
    assert hits[0].node_id == node_capacitor
    assert hits[0].version == 1

    stemmed = fulltext_search(index_conn, project_id, "capacitors")
    assert [hit.node_version_id for hit in stemmed] == [version_capacitor]

    both = fulltext_search(index_conn, project_id, "battery OR capacitor")
    assert {hit.node_version_id for hit in both} == {version_capacitor, version_battery}

    assert fulltext_search(index_conn, project_id, "", k=5) == []
    assert len(fulltext_search(index_conn, project_id, "battery OR capacitor", k=1)) == 1
    with pytest.raises(ValueError, match="k must be positive"):
        fulltext_search(index_conn, project_id, "battery", k=0)


def test_fulltext_search_scopes_to_project(index_conn) -> None:
    text = "Shared ferret quantum passage"
    project_a = seed_project(index_conn, uuid4().hex)
    project_b = seed_project(index_conn, uuid4().hex)
    node_a = seed_node(index_conn, project_a, "a")
    seed_node_version(index_conn, project_a, node_a, text)
    seed_node_version(index_conn, project_b, seed_node(index_conn, project_b, "b"), text)

    hits = fulltext_search(index_conn, project_a, "ferret")

    assert len(hits) == 1
    assert hits[0].node_id == node_a


def test_hybrid_fetch_unions_by_node_id(index_conn) -> None:
    project_id = seed_project(index_conn, uuid4().hex)
    node_ann = seed_node(index_conn, project_id, "ann")
    node_fts = seed_node(index_conn, project_id, "fts")
    node_far = seed_node(index_conn, project_id, "far")
    version_ann = seed_node_version(index_conn, project_id, node_ann, "vector page")
    seed_node_version(index_conn, project_id, node_fts, "unique ferret quantum")
    seed_node_version(index_conn, project_id, node_far, "distant page")
    seed_embedding(index_conn, project_id, version_ann, Model.A, basis_vector(0))
    seed_edge(index_conn, project_id, node_ann, node_far)

    hits = hybrid_fetch(
        index_conn,
        project_id,
        query_vector=basis_vector(0),
        query_text="ferret",
        model=Model.A,
        hops=1,
    )

    # ann: node_ann; fts: node_fts; graph seeded by both, reaching node_far.
    assert hits.node_ids == [node_ann, node_fts, node_far]
    assert [hit.node_id for hit in hits.ann] == [node_ann]
    assert [hit.node_id for hit in hits.fts] == [node_fts]
    assert {hit.node_id: hit.depth for hit in hits.graph} == {
        node_ann: 0,
        node_fts: 0,
        node_far: 1,
    }

    seeds_only = hybrid_fetch(index_conn, project_id, seed_node_ids=[node_ann], hops=1)
    assert seeds_only.node_ids == [node_ann, node_far]
    assert seeds_only.ann == [] and seeds_only.fts == []

    with pytest.raises(ValueError, match="query_vector, query_text, or seed_node_ids"):
        hybrid_fetch(index_conn, project_id)


def test_hybrid_fetch_drops_stale_seeds(index_conn) -> None:
    project_id = seed_project(index_conn, uuid4().hex)
    node_a = seed_node(index_conn, project_id, "a")
    foreign = seed_node(index_conn, seed_project(index_conn, uuid4().hex), "foreign")

    hits = hybrid_fetch(index_conn, project_id, seed_node_ids=[node_a, foreign])

    assert hits.node_ids == [node_a]
    assert [hit.node_id for hit in hits.graph] == [node_a]
