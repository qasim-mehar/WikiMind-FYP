"""Incremental indexing: idempotency, incrementality, metering, project resolution."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from index_seed import seed_node, seed_node_version, seed_project

from wikimind.index import (
    INDEX_CALL_SITE,
    IndexResult,
    embedder_model_label,
    index_project,
    resolve_project,
)
from wikimind.index.store import stored_version_ids
from wikimind.llm import CostMeter, FakeEmbedder, InMemorySink
from wikimind.llm.cost import JsonlCostSink, read_cost_events
from wikimind.prices import EMBED_DIM, FAKE_EMBED_MODEL


class IdentitylessEmbedder:
    """Minimal Embedder with no model attribute, for the label fallback path."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]


@pytest.fixture
def index_conn(test_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        yield conn


def _seed_two_versions(conn: psycopg.Connection, name: str):
    project_id = seed_project(conn, name)
    node_id = seed_node(conn, project_id, "alpha")
    version_a = seed_node_version(conn, project_id, node_id, "alpha page one")
    version_b = seed_node_version(conn, project_id, node_id, "alpha page two", version=2)
    return project_id, version_a, version_b


def test_index_embeds_all_versions_and_meters_the_ledger(index_conn, tmp_path) -> None:
    ledger = tmp_path / "costs.jsonl"
    sink = InMemorySink()
    meter = CostMeter(sink)
    embedder = FakeEmbedder(meter=meter, call_site=INDEX_CALL_SITE)
    project_id, version_a, version_b = _seed_two_versions(index_conn, uuid4().hex)

    result = index_project(index_conn, project_id, embedder, model=FAKE_EMBED_MODEL)

    assert result == IndexResult(
        project_id=project_id, model=FAKE_EMBED_MODEL, total=2, embedded=2, skipped=0
    )
    assert stored_version_ids(index_conn, project_id, FAKE_EMBED_MODEL) == {
        version_a,
        version_b,
    }

    event = meter.events[0]
    assert event.call_site == INDEX_CALL_SITE
    assert event.model == FAKE_EMBED_MODEL
    assert event.shadow_cost_usd == 0.0
    assert event.meta["texts"] == 2
    assert event.meta["dim"] == EMBED_DIM
    assert sink.events == meter.events
    # The same events must reach a real JSONL ledger, not just memory.
    ledger_meter = CostMeter(JsonlCostSink(ledger))
    other_project = seed_project(index_conn, uuid4().hex)
    seed_node_version(
        index_conn,
        other_project,
        seed_node(index_conn, other_project, "alpha"),
        "ledger check text",
    )
    index_project(
        index_conn,
        other_project,
        FakeEmbedder(meter=ledger_meter, call_site=INDEX_CALL_SITE),
        model=FAKE_EMBED_MODEL,
    )
    stored_events = read_cost_events(ledger)
    assert [event.call_site for event in stored_events] == [INDEX_CALL_SITE]


def test_index_rerun_is_idempotent(index_conn) -> None:
    meter = CostMeter(InMemorySink())
    embedder = FakeEmbedder(meter=meter, call_site=INDEX_CALL_SITE)
    project_id, _, _ = _seed_two_versions(index_conn, uuid4().hex)

    first = index_project(index_conn, project_id, embedder, model=FAKE_EMBED_MODEL)
    second = index_project(index_conn, project_id, embedder, model=FAKE_EMBED_MODEL)

    assert first.embedded == 2
    assert second == IndexResult(
        project_id=project_id, model=FAKE_EMBED_MODEL, total=2, embedded=0, skipped=2
    )
    # Nothing was re-embedded: the embedder was never called a second time.
    assert len(meter.events) == 1


def test_index_is_incremental_over_new_versions(index_conn) -> None:
    meter = CostMeter(InMemorySink())
    embedder = FakeEmbedder(meter=meter, call_site=INDEX_CALL_SITE)
    project_id, version_a, version_b = _seed_two_versions(index_conn, uuid4().hex)
    index_project(index_conn, project_id, embedder, model=FAKE_EMBED_MODEL)

    node_id = seed_node(index_conn, project_id, "beta")
    version_c = seed_node_version(index_conn, project_id, node_id, "beta page")
    third = index_project(index_conn, project_id, embedder, model=FAKE_EMBED_MODEL)

    assert third == IndexResult(
        project_id=project_id, model=FAKE_EMBED_MODEL, total=3, embedded=1, skipped=2
    )
    assert stored_version_ids(index_conn, project_id, FAKE_EMBED_MODEL) == {
        version_a,
        version_b,
        version_c,
    }
    assert len(meter.events) == 2  # one batch per run


def test_index_batches_embedder_calls_deterministically(index_conn) -> None:
    meter = CostMeter(InMemorySink())
    embedder = FakeEmbedder(meter=meter, call_site=INDEX_CALL_SITE)
    project_id = seed_project(index_conn, uuid4().hex)
    node_id = seed_node(index_conn, project_id, "alpha")
    for number in range(1, 4):
        seed_node_version(index_conn, project_id, node_id, f"text {number}", version=number)

    result = index_project(index_conn, project_id, embedder, model=FAKE_EMBED_MODEL, batch_size=2)

    assert result.embedded == 3
    assert [event.meta["texts"] for event in meter.events] == [2, 1]


def test_index_scopes_to_one_project(index_conn) -> None:
    meter = CostMeter(InMemorySink())
    embedder = FakeEmbedder(meter=meter, call_site=INDEX_CALL_SITE)
    project_a = seed_project(index_conn, uuid4().hex)
    seed_node_version(
        index_conn,
        project_a,
        seed_node(index_conn, project_a, "alpha"),
        "project a text",
    )
    project_b = seed_project(index_conn, uuid4().hex)
    seed_node_version(
        index_conn,
        project_b,
        seed_node(index_conn, project_b, "alpha"),
        "project b text",
    )

    result = index_project(index_conn, project_a, embedder, model=FAKE_EMBED_MODEL)

    assert result.total == 1
    assert stored_version_ids(index_conn, project_b, FAKE_EMBED_MODEL) == set()


def test_index_tracks_models_separately(index_conn) -> None:
    embedder = FakeEmbedder(call_site=INDEX_CALL_SITE)
    project_id, version_a, version_b = _seed_two_versions(index_conn, uuid4().hex)

    index_project(index_conn, project_id, embedder, model="model-one")
    index_project(index_conn, project_id, embedder, model="model-two")

    assert stored_version_ids(index_conn, project_id, "model-one") == {
        version_a,
        version_b,
    }
    assert stored_version_ids(index_conn, project_id, "model-two") == {
        version_a,
        version_b,
    }


def test_index_rejects_wrong_dimension_before_writing(index_conn) -> None:
    embedder = FakeEmbedder(dim=8, call_site=INDEX_CALL_SITE)
    project_id, _, _ = _seed_two_versions(index_conn, uuid4().hex)

    with pytest.raises(ValueError, match="expected 384"):
        index_project(index_conn, project_id, embedder, model=FAKE_EMBED_MODEL)
    assert stored_version_ids(index_conn, project_id, FAKE_EMBED_MODEL) == set()


def test_index_rejects_bad_batch_size(index_conn) -> None:
    embedder = FakeEmbedder(call_site=INDEX_CALL_SITE)
    project_id = seed_project(index_conn, uuid4().hex)
    with pytest.raises(ValueError, match="batch_size"):
        index_project(index_conn, project_id, embedder, model=FAKE_EMBED_MODEL, batch_size=0)


def test_embedder_model_label_is_derived_or_required() -> None:
    assert embedder_model_label(FakeEmbedder()) == FAKE_EMBED_MODEL
    with pytest.raises(ValueError, match="model identity"):
        embedder_model_label(IdentitylessEmbedder())


def test_resolve_project_by_uuid_name_missing_and_ambiguous(index_conn) -> None:
    project_id = seed_project(index_conn, "resolve-target")

    resolved = resolve_project(index_conn, str(project_id))
    assert resolved.id == project_id
    assert resolved.name == "resolve-target"
    assert resolve_project(index_conn, "resolve-target").id == project_id

    with pytest.raises(KeyError):
        resolve_project(index_conn, str(uuid4()))
    with pytest.raises(KeyError):
        resolve_project(index_conn, "no-such-project")

    seed_project(index_conn, "resolve-target")
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_project(index_conn, "resolve-target")
