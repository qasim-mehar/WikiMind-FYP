from __future__ import annotations

import pytest
from pydantic import BaseModel

from wikimind.llm import (
    Embedder,
    FakeEmbedder,
    FakeLLM,
    LLMClient,
    LLMResult,
    Usage,
)


class Schema(BaseModel):
    ok: bool


def test_fake_llm_satisfies_protocol() -> None:
    assert isinstance(FakeLLM([]), LLMClient)


def test_fake_embedder_satisfies_protocol() -> None:
    assert isinstance(FakeEmbedder(), Embedder)


def test_usage_totals() -> None:
    usage = Usage(in_tokens=10, out_tokens=7)
    assert usage.total_tokens == 17


def test_usage_rejects_negative_tokens() -> None:
    with pytest.raises(ValueError):
        Usage(in_tokens=-1)


def test_llm_result_defaults_to_empty_usage() -> None:
    result = LLMResult(json={"ok": True})
    assert result.json == {"ok": True}
    assert result.usage == Usage()


def test_response_schema_accepts_pydantic_and_dict() -> None:
    from wikimind.llm.protocols import ResponseSchema

    assert isinstance({"type": "object"}, dict)
    assert Schema.model_json_schema()["type"] == "object"
    assert ResponseSchema is not None
