"""OpenRouter judge client (REST via httpx). Never a Gemini model."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel

from ..prices import JUDGE_MODEL, JUDGE_SHADOW_MODEL
from .cost import CostMeter, validate_call_site
from .errors import (
    JudgeError,
    MissingAPIKeyError,
    ResponseFormatError,
    RetryExhaustedError,
)
from .json_utils import parse_json_object
from .protocols import LLMResult, ResponseSchema, Usage
from .retry import call_with_retries
from .secrets import openrouter_api_key, redact_secrets

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


class _RetryableHTTPStatus(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}")


def is_retryable_http_status(exc: Exception) -> bool:
    return isinstance(exc, _RetryableHTTPStatus) and (
        exc.status_code == 429 or 500 <= exc.status_code <= 599
    )


def _schema_json(response_schema: ResponseSchema) -> dict[str, Any] | None:
    if response_schema is None:
        return None
    if isinstance(response_schema, dict):
        return response_schema
    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
        return response_schema.model_json_schema()
    raise TypeError(
        "response_schema must be a JSON-schema dict or a Pydantic model class"
    )


class JudgeClient:
    """Metered OpenRouter judge client.

    The endpoint is a free model; shadow cost is priced at the paid twin.
    ``max_retries`` is the total attempt count.
    """

    def __init__(
        self,
        *,
        meter: CostMeter,
        api_key: str | None = None,
        model: str = JUDGE_MODEL,
        base_url: str = OPENROUTER_CHAT_URL,
        http_client: httpx.Client | None = None,
        timeout: float = 60.0,
        temperature: float | None = 0.0,
        max_tokens: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        base_delay: float = 0.5,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.meter = meter
        self.model = model
        self.base_url = base_url
        self._api_key = api_key
        self._http_client = http_client
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._sleep = sleep
        self._base_delay = base_delay
        self.meta = dict(meta or {})

    def _ensure_key(self) -> str:
        key = self._api_key or openrouter_api_key()
        if key is None:
            raise MissingAPIKeyError("OPENROUTER_API_KEY is not set")
        return key

    def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        if self._http_client is not None:
            return self._http_client.post(self.base_url, json=payload, headers=headers)
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(self.base_url, json=payload, headers=headers)

    def _build_payload(
        self, system: str, user: str, response_schema: ResponseSchema
    ) -> dict[str, Any]:
        system_content = system
        schema = _schema_json(response_schema)
        if schema is not None:
            system_content = (
                f"{system}\n\nRespond with a single JSON object that conforms to "
                "this JSON Schema:\n"
                f"{json.dumps(schema, sort_keys=True)}"
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        return payload

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
        key = self._ensure_key()
        payload = self._build_payload(system, user, response_schema)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "WikiMind",
        }

        def attempt() -> httpx.Response:
            try:
                response = self._post(payload, headers)
            except httpx.HTTPError as exc:
                raise JudgeError(f"OpenRouter transport error: {exc}") from None
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise _RetryableHTTPStatus(
                    response.status_code, redact_secrets(response.text[:2000])
                )
            return response

        try:
            response = call_with_retries(
                attempt,
                max_attempts=max_retries,
                is_retryable=is_retryable_http_status,
                sleep=self._sleep,
                base_delay=self._base_delay,
            )
        except _RetryableHTTPStatus as exc:
            raise RetryExhaustedError(
                f"OpenRouter judge call failed after {max_retries} attempts: "
                f"HTTP {exc.status_code}: {exc.body}"
            ) from None

        if response.status_code >= 400:
            body = redact_secrets(response.text[:2000])
            raise JudgeError(
                f"OpenRouter judge call failed with HTTP {response.status_code}: {body}"
            )

        try:
            data = response.json()
        except ValueError:
            raise JudgeError("OpenRouter returned a non-JSON HTTP body") from None

        choices = data.get("choices") or []
        if not choices:
            raise JudgeError("OpenRouter response contained no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict)
            )
        try:
            parsed = parse_json_object(content if isinstance(content, str) else None)
        except ValueError:
            raise ResponseFormatError(
                "OpenRouter judge response was not a JSON object"
            ) from None

        usage_data = data.get("usage") or {}
        usage = Usage(
            in_tokens=int(usage_data.get("prompt_tokens") or 0),
            out_tokens=int(usage_data.get("completion_tokens") or 0),
        )

        completion_details = usage_data.get("completion_tokens_details") or {}
        event_meta = dict(self.meta)
        if meta:
            event_meta.update(meta)
        event_meta.update(
            {
                "provider": "openrouter",
                "endpoint": self.base_url,
                "pricing_basis": "paid_twin",
                "paid_twin": JUDGE_SHADOW_MODEL,
                "actual_cost_usd": 0.0,
                "response_model": data.get("model"),
                "generation_id": data.get("id"),
                "finish_reason": choices[0].get("finish_reason"),
                "reasoning_tokens": completion_details.get("reasoning_tokens"),
            }
        )

        self.meter.record(
            call_site=call_site,
            model=self.model,
            in_tokens=usage.in_tokens,
            out_tokens=usage.out_tokens,
            meta=event_meta,
        )
        return LLMResult(json=parsed, usage=usage)
