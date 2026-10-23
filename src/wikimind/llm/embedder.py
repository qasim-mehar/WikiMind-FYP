"""Embedders: real sentence-transformers model plus a deterministic fake."""

from __future__ import annotations

import hashlib
import math
import os
import struct
from typing import Any

from ..prices import EMBED_DIM, EMBED_MODEL, LOCAL_EMBED_PRICE
from .cost import CostMeter
from .errors import EmbeddingError


class FakeEmbedder:
    """Deterministic hash-derived vectors, identical across runs/platforms."""

    model = "fake-embedder"

    def __init__(
        self,
        *,
        dim: int = EMBED_DIM,
        meter: CostMeter | None = None,
        call_site: str = "embed.fake",
        meta: dict[str, Any] | None = None,
    ) -> None:
        if dim < 1:
            raise ValueError("dim must be positive")
        self.dim = dim
        self.meter = meter
        self.call_site = call_site
        self.meta = dict(meta or {})

    def _vector(self, text: str) -> list[float]:
        raw = hashlib.shake_256(text.encode("utf-8")).digest(self.dim * 4)
        values = struct.unpack(f"<{self.dim}I", raw)
        vector = [(value / 4_294_967_295.0) * 2.0 - 1.0 for value in values]
        norm = math.sqrt(sum(component * component for component in vector)) or 1.0
        return [component / norm for component in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [self._vector(text) for text in texts]
        if self.meter is not None:
            self.meter.record(
                call_site=self.call_site,
                model=self.model,
                in_tokens=sum(max(1, len(text) // 4) for text in texts),
                out_tokens=0,
                meta={**self.meta, "texts": len(texts), "dim": self.dim},
                unit_price=LOCAL_EMBED_PRICE,
            )
        return vectors


class SentenceTransformerEmbedder:
    """Local CPU embedder; the model is loaded lazily and cached by HF."""

    def __init__(
        self,
        *,
        meter: CostMeter,
        model_name: str = EMBED_MODEL,
        device: str = "cpu",
        cache_folder: str | None = None,
        call_site: str = "embed.default",
        meta: dict[str, Any] | None = None,
        model: Any | None = None,
    ) -> None:
        self.meter = meter
        self.model_name = model_name
        self.device = device
        self.cache_folder = (
            cache_folder
            if cache_folder is not None
            else os.environ.get("WIKIMIND_EMBED_CACHE")
        )
        self.call_site = call_site
        self.meta = dict(meta or {})
        self._model = model

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise EmbeddingError(
                "sentence-transformers is not installed; install the 'embed' extra"
            ) from exc
        try:
            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
                cache_folder=self.cache_folder,
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(
                f"could not load embedding model {self.model_name!r}: {exc}"
            ) from None
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        try:
            encoded = model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"embedding failed: {exc}") from None

        vectors = [[float(component) for component in vector] for vector in encoded]
        for vector in vectors:
            if len(vector) != EMBED_DIM:
                raise EmbeddingError(
                    f"embedder returned dim {len(vector)}, expected {EMBED_DIM}"
                )

        self.meter.record(
            call_site=self.call_site,
            model=self.model_name,
            in_tokens=sum(max(1, len(text) // 4) for text in texts),
            out_tokens=0,
            meta={**self.meta, "texts": len(texts), "dim": EMBED_DIM},
            unit_price=LOCAL_EMBED_PRICE,
        )
        return vectors
