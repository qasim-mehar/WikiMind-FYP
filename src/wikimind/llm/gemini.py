"""Gemini client (google-genai SDK), JSON mode, metered, 429/5xx retries only."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ..prices import GEMINI_MODEL, price_for
from .cost import CostMeter, validate_call_site
from .errors import (
    GeminiError,
    MissingAPIKeyError,
    ResponseFormatError,
    RetryExhaustedError,
)
from .json_utils import json_safe_value, parse_json_object
from .protocols import LLMResult, ResponseSchema, Usage
from .retry import call_with_retries
from .secrets import gemini_api_key, redact
from .tokens import token_count


def _error_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        if code.isdigit():
            return int(code)
        if code == "RESOURCE_EXHAUSTED":
            return 429
        if code in {"UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED"}:
            return 503
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def is_retryable_gemini_error(exc: Exception) -> bool:
    code = _error_code(exc)
    return code == 429 or (code is not None and 500 <= code <= 599)


class GeminiClient:
    """Metered Gemini JSON client.

    ``max_retries`` passed to ``complete`` is the total attempt count.
    """

    def __init__(
        self,
        *,
        meter: CostMeter,
        api_key: str | None = None,
        model: str = GEMINI_MODEL,
        temperature: float | None = 0.0,
        max_output_tokens: int | None = None,
        client: Any | None = None,
        timeout: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
        base_delay: float = 0.5,
        meta: dict[str, Any] | None = None,
    ) -> None:
        price_for(model)
        self.meter = meter
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self._api_key = api_key
        self._client = client
        self.timeout = timeout
        self._sleep = sleep
        self._base_delay = base_delay
        self.meta = dict(meta or {})

    def _redact(self, text: str) -> str:
        secrets = [self._api_key] if self._api_key else None
        return redact(text, secrets)

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        key = self._api_key or gemini_api_key()
        if key is None:
            raise MissingAPIKeyError("GEMINI_API_KEY is not set")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise GeminiError(
                "google-genai is not installed; install the 'llm' extra"
            ) from exc
        self._client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(timeout=int(self.timeout * 1000)),
        )
        return self._client

    def _build_config(self, system: str, response_schema: ResponseSchema) -> Any:
        from google.genai import types

        kwargs: dict[str, Any] = {
            "system_instruction": system,
            "response_mime_type": "application/json",
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_output_tokens is not None:
            kwargs["max_output_tokens"] = self.max_output_tokens
        if response_schema is not None:
            if isinstance(response_schema, dict):
                kwargs["response_json_schema"] = response_schema
            elif isinstance(response_schema, type) and issubclass(
                response_schema, BaseModel
            ):
                kwargs["response_schema"] = response_schema
            else:
                raise TypeError(
                    "response_schema must be a JSON-schema dict or a Pydantic model class"
                )
        return types.GenerateContentConfig(**kwargs)

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
        try:
            config = self._build_config(system, response_schema)
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise GeminiError(
                "google-genai is not installed; install the 'llm' extra"
            ) from exc

        def attempt() -> Any:
            return self._ensure_client().models.generate_content(
                model=self.model,
                contents=user,
                config=config,
            )

        try:
            response = call_with_retries(
                attempt,
                max_attempts=max_retries,
                is_retryable=is_retryable_gemini_error,
                sleep=self._sleep,
                base_delay=self._base_delay,
            )
        except MissingAPIKeyError:
            raise
        except Exception as exc:  # noqa: BLE001
            if is_retryable_gemini_error(exc):
                raise RetryExhaustedError(
                    self._redact(
                        f"Gemini call failed after {max_retries} attempts: {exc}"
                    )
                ) from None
            raise GeminiError(self._redact(f"Gemini call failed: {exc}")) from None

        usage_metadata = getattr(response, "usage_metadata", None)
        in_tokens, in_ok = token_count(
            getattr(usage_metadata, "prompt_token_count", None)
        )
        candidates_tokens, candidates_ok = token_count(
            getattr(usage_metadata, "candidates_token_count", None)
        )
        thinking_tokens, _ = token_count(
            getattr(usage_metadata, "thoughts_token_count", None)
        )
        usage_reported = usage_metadata is not None
        # thoughts_token_count is optional (non-thinking models omit it), so a
        # missing thought count does not invalidate otherwise-complete usage.
        usage_invalid = not usage_reported or not (in_ok and candidates_ok)
        usage = Usage(
            in_tokens=in_tokens,
            out_tokens=candidates_tokens + thinking_tokens,
        )

        parse_error: Exception | None = None
        payload: dict[str, Any] | None = None
        try:
            response_text = response.text
        except Exception as exc:  # noqa: BLE001
            response_text = None
            parse_error = exc
        try:
            payload = parse_json_object(response_text)
        except Exception as exc:  # noqa: BLE001
            parse_error = parse_error or exc
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, BaseModel):
                payload = parsed.model_dump(mode="json")
            elif isinstance(parsed, dict):
                payload = parsed

        schema_checked = False
        schema_ok: bool | None = None
        if (
            payload is not None
            and isinstance(response_schema, type)
            and issubclass(response_schema, BaseModel)
        ):
            schema_checked = True
            try:
                payload = response_schema.model_validate(payload).model_dump(
                    mode="json"
                )
                schema_ok = True
            except Exception as exc:  # noqa: BLE001
                schema_ok = False
                parse_error = parse_error or exc

        event_meta.setdefault("provider", "google-genai")
        response_model_version = json_safe_value(
            getattr(response, "model_version", None)
        )
        if response_model_version:
            event_meta["model_version"] = response_model_version
        else:
            event_meta.setdefault("model_version", self.model)
        event_meta["response_id"] = json_safe_value(
            getattr(response, "response_id", None)
        )
        event_meta["thinking_tokens"] = thinking_tokens
        event_meta["usage_reported"] = usage_reported
        event_meta["usage_invalid"] = usage_invalid
        event_meta["parse_ok"] = payload is not None
        event_meta["schema_checked"] = schema_checked
        event_meta["schema_ok"] = schema_ok
        self.meter.validate_meta(event_meta)

        self.meter.record(
            call_site=call_site,
            model=self.model,
            in_tokens=usage.in_tokens,
            out_tokens=usage.out_tokens,
            meta=event_meta,
        )

        if payload is None:
            raise ResponseFormatError(
                self._redact(f"Gemini response was not a JSON object: {parse_error}")
            ) from None
        if schema_ok is False:
            raise ResponseFormatError(
                self._redact(
                    f"Gemini response did not match {response_schema.__name__}: "
                    f"{parse_error}"
                )
            ) from None
        return LLMResult(json=payload, usage=usage)
