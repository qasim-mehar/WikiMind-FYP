"""Keyless fakes for tests. They never touch the network."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..prices import FAKE_LLM_MODEL, price_for
from .cost import CostMeter, validate_call_site
from .embedder import FakeEmbedder
from .json_utils import parse_json_object
from .protocols import LLMResult, ResponseSchema, Usage

__all__ = ["FakeCall", "FakeEmbedder", "FakeLLM"]


@dataclass(frozen=True, slots=True)
class FakeCall:
    system: str
    user: str
    response_schema: ResponseSchema
    call_site: str
    max_retries: int
    meta: dict[str, Any] = field(default_factory=dict)


class FakeLLM:
    """Scripted queue of JSON responses that records every call."""

    def __init__(
        self,
        responses: Iterable[Any] | Any,
        *,
        usage: Usage | None = None,
        model: str = FAKE_LLM_MODEL,
        meter: CostMeter | None = None,
    ) -> None:
        price_for(model)
        if isinstance(responses, (dict, str, LLMResult, BaseException)) or callable(responses):
            responses = [responses]
        self._responses: deque[Any] = deque(responses)
        self.usage = usage or Usage()
        self.model = model
        self.meter = meter
        self.calls: list[FakeCall] = []

    @property
    def remaining(self) -> int:
        return len(self._responses)

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: ResponseSchema,
        call_site: str,
        max_retries: int = 3,
        meta: dict[str, Any] | None = None,
    ) -> LLMResult:
        validate_call_site(call_site)
        if not 1 <= max_retries <= 3:
            raise ValueError("max_retries must be between 1 and 3")
        self.calls.append(
            FakeCall(
                system=system,
                user=user,
                response_schema=response_schema,
                call_site=call_site,
                max_retries=max_retries,
                meta=dict(meta or {}),
            )
        )
        if not self._responses:
            raise AssertionError("FakeLLM has no scripted responses left")

        item = self._responses.popleft()
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(
                system=system,
                user=user,
                response_schema=response_schema,
                call_site=call_site,
                max_retries=max_retries,
                meta=meta,
            )

        if isinstance(item, LLMResult):
            result = item
        elif isinstance(item, str):
            result = LLMResult(json=parse_json_object(item), usage=self.usage)
        elif isinstance(item, dict):
            result = LLMResult(json=item, usage=self.usage)
        else:
            raise TypeError(
                "FakeLLM responses must be dicts, JSON strings, LLMResult, exceptions, or callables"
            )

        if self.meter is not None:
            self.meter.record(
                call_site=call_site,
                model=self.model,
                in_tokens=result.usage.in_tokens,
                out_tokens=result.usage.out_tokens,
                meta={"provider": "fake", **(meta or {})},
            )
        return result
