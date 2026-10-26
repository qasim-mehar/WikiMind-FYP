from __future__ import annotations

import hashlib
import json
import math

import pytest

from wikimind.llm import (
    CostMeter,
    EmbeddingError,
    FakeEmbedder,
    InMemorySink,
    SentenceTransformerEmbedder,
)
from wikimind.prices import EMBED_DIM, EMBED_MODEL


class FakeModel:
    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim
        self.calls: list[tuple[list[str], dict]] = []

    def encode(self, texts: list[str], **kwargs):
        self.calls.append((list(texts), dict(kwargs)))
        return [[0.0] * (self.dim - 1) + [1.0] for _ in texts]


def test_fake_embedder_is_deterministic() -> None:
    first = FakeEmbedder()
    second = FakeEmbedder()
    a = first.embed(["same text"])[0]
    b = second.embed(["same text"])[0]
    assert a == b
    assert len(a) == EMBED_DIM
    assert math.isclose(sum(value * value for value in a), 1.0, rel_tol=1e-9)


def test_fake_embedder_differs_by_text() -> None:
    embedder = FakeEmbedder()
    a, b = embedder.embed(["alpha", "beta"])
    assert a != b


# SHA-256 of the JSON serialization of FakeEmbedder().embed(["same text"])[0].
# Pinned so any platform/run drift in the hash-derived vectors fails loudly.
FAKE_VECTOR_DIGEST = "e87e398fd6730ad968d54263555f4066961b861ed803d3a2c09498c24dee9676"


def test_fake_embedder_vectors_are_pinned_across_platforms() -> None:
    vector = FakeEmbedder().embed(["same text"])[0]
    digest = hashlib.sha256(json.dumps(vector).encode("utf-8")).hexdigest()
    assert digest == FAKE_VECTOR_DIGEST


def test_fake_embedder_empty_input() -> None:
    assert FakeEmbedder().embed([]) == []


def test_fake_embedder_meters_at_zero_cost() -> None:
    meter = CostMeter(InMemorySink())
    embedder = FakeEmbedder(meter=meter)
    embedder.embed(["a", "b"])
    event = meter.events[0]
    assert event.call_site == "embed.fake"
    assert event.model == "fake-embedder"
    assert event.shadow_cost_usd == 0.0
    assert event.meta["texts"] == 2
    assert event.meta["dim"] == EMBED_DIM


def test_real_embedder_wrapper_uses_normalized_encode() -> None:
    meter = CostMeter(InMemorySink())
    model = FakeModel()
    embedder = SentenceTransformerEmbedder(meter=meter, model=model)
    vectors = embedder.embed(["one", "two"])

    assert len(vectors) == 2
    assert all(len(vector) == EMBED_DIM for vector in vectors)
    texts, kwargs = model.calls[0]
    assert texts == ["one", "two"]
    assert kwargs["normalize_embeddings"] is True
    assert kwargs["show_progress_bar"] is False
    assert kwargs["convert_to_numpy"] is True

    event = meter.events[0]
    assert event.model == EMBED_MODEL
    assert event.call_site == "embed.default"
    assert event.shadow_cost_usd == 0.0


def test_real_embedder_is_lazy() -> None:
    meter = CostMeter(InMemorySink())
    embedder = SentenceTransformerEmbedder(meter=meter)
    assert embedder._model is None
    assert embedder.model_name == EMBED_MODEL
    assert embedder.device == "cpu"


def test_real_embedder_rejects_wrong_dim() -> None:
    meter = CostMeter(InMemorySink())
    embedder = SentenceTransformerEmbedder(meter=meter, model=FakeModel(dim=8))
    with pytest.raises(EmbeddingError):
        embedder.embed(["text"])


def test_real_embedder_empty_input_does_not_load_model() -> None:
    meter = CostMeter(InMemorySink())
    embedder = SentenceTransformerEmbedder(meter=meter)
    assert embedder.embed([]) == []
    assert embedder._model is None
    assert meter.events == []


def test_fake_embedder_rejects_bad_call_site() -> None:
    embedder = FakeEmbedder(call_site="ingest.test")
    with pytest.raises(ValueError):
        embedder.embed(["text"])


def test_real_embedder_rejects_unpriced_model() -> None:
    meter = CostMeter(InMemorySink())
    with pytest.raises(ValueError):
        SentenceTransformerEmbedder(meter=meter, model_name="unknown/model")
