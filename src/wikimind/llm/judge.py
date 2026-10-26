"""OpenRouter judge client (REST via httpx). Never a Gemini model."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel

from ..prices import JUDGE_MODEL, JUDGE_SHADOW_MODEL, price_for
from .cost import CostMeter, validate_call_site
from .errors import (
    JudgeError,
    MissingAPIKeyError,
    ResponseFormatError,
    RetryExhaustedError,
)
from .json_utils import json_safe_value, parse_json_object
from .protocols import LLMResult, ResponseSchema, Usage
from .retry import call_with_retries
from .secrets import openrouter_api_key, redact
from .tokens import token_count

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
        if "gemini" in model.lower():
            raise ValueError("JudgeClient must not use a Gemini model")
        price_for(model)
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
        if not 1 <= max_retries <= 3:
            raise ValueError("max_retries must be between 1 and 3")
        event_meta = dict(self.meta)
        if meta:
            event_meta.update(meta)
        self.meter.validate_meta(event_meta)
        self.meter.preflight()
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
                raise JudgeError(
                    redact(f"OpenRouter transport error: {exc}", [key])
                ) from None
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise _RetryableHTTPStatus(
                    response.status_code, redact(response.text[:2000], [key])
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

        if not 200 <= response.status_code < 300:
            body = redact(response.text[:2000], [key])
            raise JudgeError(
                f"OpenRouter judge call failed with HTTP {response.status_code}: {body}"
            )

        data: Any = None
        parse_error: Exception | None = None
        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            parse_error = exc

        usage_data: dict[str, Any] = {}
        usage_reported = False
        if isinstance(data, dict):
            raw_usage = data.get("usage")
            if isinstance(raw_usage, dict):
                usage_data = raw_usage
                usage_reported = True
        in_tokens, in_ok = token_count(usage_data.get("prompt_tokens"))
        out_tokens, out_ok = token_count(usage_data.get("completion_tokens"))
        usage_invalid = not usage_reported or not (in_ok and out_ok)
        usage = Usage(in_tokens=in_tokens, out_tokens=out_tokens)

        completion_details = usage_data.get("completion_tokens_details")
        if not isinstance(completion_details, dict):
            completion_details = {}
        reasoning_tokens, reasoning_ok = token_count(
            completion_details.get("reasoning_tokens")
        )

        response_model = json_safe_value(
            data.get("model") if isinstance(data, dict) else None
        )
        generation_id = json_safe_value(
            data.get("id") if isinstance(data, dict) else None
        )
        finish_reason: Any = None
        structural_error: str | None = None
        parsed: dict[str, Any] | None = None

        if not isinstance(data, dict):
            structural_error = "OpenRouter response was not a JSON object"
        else:
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices:
                structural_error = "OpenRouter response contained no choices"
            else:
                first = choices[0]
                if not isinstance(first, dict):
                    structural_error = "OpenRouter choice was not an object"
                else:
                    finish_reason = json_safe_value(first.get("finish_reason"))
                    message = first.get("message")
                    if not isinstance(message, dict):
                        structural_error = "OpenRouter choice had no message object"
                    else:
                        content = message.get("content")
                        if isinstance(content, list):
                            parts: list[str] = []
                            for part in content:
                                if not isinstance(part, dict):
                                    continue
                                text_part = part.get("text")
                                if isinstance(text_part, str):
                                    parts.append(text_part)
                            content = "".join(parts)
                        if not isinstance(content, str):
                            structural_error = "OpenRouter message content was not text"
                        else:
                            try:
                                parsed = parse_json_object(content)
                            except Exception as exc:  # noqa: BLE001
                                parse_error = exc

        schema_checked = False
        schema_ok: bool | None = None
        if (
            parsed is not None
            and isinstance(response_schema, type)
            and issubclass(response_schema, BaseModel)
        ):
            schema_checked = True
            try:
                parsed = response_schema.model_validate(parsed).model_dump(mode="json")
                schema_ok = True
            except Exception as exc:  # noqa: BLE001
                schema_ok = False
                parse_error = parse_error or exc

        actual_cost_usd = (
            0.0
            if self.model == JUDGE_MODEL
            else price_for(self.model).cost_usd(usage.in_tokens, usage.out_tokens)
        )
        event_meta.update(
            {
                "endpoint": json_safe_value(self.base_url),
                "pricing_basis": "paid_twin",
                "paid_twin": JUDGE_SHADOW_MODEL,
                "actual_cost_usd": actual_cost_usd,
                "response_model": response_model,
                "generation_id": generation_id,
                "finish_reason": finish_reason,
                "reasoning_tokens": reasoning_tokens if reasoning_ok else None,
                "usage_reported": usage_reported,
                "usage_invalid": usage_invalid,
                "parse_ok": parsed is not None,
                "schema_checked": schema_checked,
                "schema_ok": schema_ok,
            }
        )
        # Match GeminiClient: a caller-supplied "provider" tag wins.
        event_meta.setdefault("provider", "openrouter")
        self.meter.validate_meta(event_meta)

        self.meter.record(
            call_site=call_site,
            model=self.model,
            in_tokens=usage.in_tokens,
            out_tokens=usage.out_tokens,
            meta=event_meta,
        )

        if structural_error is not None:
            raise ResponseFormatError(
                redact(
                    f"OpenRouter judge response was invalid: {structural_error}",
                    [key],
                )
            ) from None
        if parsed is None:
            raise ResponseFormatError(
                redact(
                    f"OpenRouter judge response was not a JSON object: {parse_error}",
                    [key],
                )
            ) from None
        if schema_ok is False:
            raise ResponseFormatError(
                redact(
                    f"OpenRouter judge response did not match "
                    f"{response_schema.__name__}: {parse_error}",
                    [key],
                )
            ) from None
        return LLMResult(json=parsed, usage=usage)
