"""Cost metering: the audit spine for every model call.

Every real provider call constructs a CostEvent and emits it to a sink.
The default sink appends JSONL to ``costs.jsonl`` (override with
``WIKIMIND_COSTS_PATH``).  A database sink is an interface stub for trunk.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from ..prices import ModelPrice, price_for
from .errors import CostSinkError

CALL_SITE_PREFIXES = ("l1.", "l2.", "qa.", "judge.", "embed.")


def validate_call_site(call_site: str) -> str:
    if not isinstance(call_site, str) or not call_site:
        raise ValueError("call_site must be a non-empty string")
    if not call_site.startswith(CALL_SITE_PREFIXES):
        allowed = ", ".join(CALL_SITE_PREFIXES)
        raise ValueError(f"call_site {call_site!r} must start with one of: {allowed}")
    if call_site.endswith("."):
        raise ValueError("call_site must not end with '.'")
    return call_site


class CostEvent(BaseModel):
    """One metered model invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_site: str
    model: str
    in_tokens: int = Field(ge=0)
    out_tokens: int = Field(ge=0)
    unit_price: ModelPrice
    shadow_cost_usd: float = Field(ge=0)
    meta: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("call_site")
    @classmethod
    def _valid_call_site(cls, value: str) -> str:
        return validate_call_site(value)

    @field_validator("timestamp")
    @classmethod
    def _aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @field_validator("meta", mode="before")
    @classmethod
    def _meta_is_json_safe(cls, value: Any) -> Any:
        if value is None:
            value = {}
        _validate_meta_dict(value)
        return value

    @model_validator(mode="after")
    def _prices_and_cost_are_consistent(self, info: ValidationInfo) -> CostEvent:
        expected_cost = self.unit_price.cost_usd(self.in_tokens, self.out_tokens)
        if round(self.shadow_cost_usd, 12) != expected_cost:
            raise ValueError(
                "shadow_cost_usd does not match tokens x unit_price: "
                f"expected {expected_cost}, got {self.shadow_cost_usd}"
            )
        if not (info.context or {}).get("historical"):
            expected_price = price_for(self.model)
            if self.unit_price != expected_price:
                raise ValueError(
                    f"unit_price does not match the audited price for {self.model!r}"
                )
        return self


def _validate_meta_dict(meta: Any) -> dict[str, Any]:
    if not isinstance(meta, dict):
        raise TypeError("cost event meta must be a dict")
    if not all(isinstance(key, str) for key in meta):
        raise ValueError("cost event meta keys must be strings")
    try:
        json.dumps(meta, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cost event meta is not JSON-serializable: {exc}") from exc
    return meta


@runtime_checkable
class CostSink(Protocol):
    def preflight(self) -> None: ...

    def emit(self, event: CostEvent) -> None: ...


class JsonlCostSink:
    """Append-only JSONL sink; the default local audit trail."""

    def __init__(self, path: str | Path | None = None, *, fsync: bool = False) -> None:
        self.path = Path(path) if path is not None else default_costs_path()
        self.fsync = fsync
        self._lock = threading.Lock()

    def preflight(self) -> None:
        """Fail before a billable call if the ledger cannot be appended to."""

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8"):
                pass
        except OSError as exc:
            raise CostSinkError(
                f"could not open cost ledger {self.path}: {exc}"
            ) from exc

    def emit(self, event: CostEvent) -> None:
        try:
            line = event.model_dump_json()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                if self.fsync:
                    os.fsync(handle.fileno())
        except (OSError, TypeError, ValueError) as exc:
            raise CostSinkError(
                f"could not write cost event to {self.path}: {exc}"
            ) from exc

    def read_events(self) -> list[CostEvent]:
        return read_cost_events(self.path)


class InMemorySink:
    """Test/CLI helper that keeps events in a list."""

    def __init__(self) -> None:
        self.events: list[CostEvent] = []

    def preflight(self) -> None:
        return None

    def emit(self, event: CostEvent) -> None:
        self.events.append(event)


class DbCostSink:
    """Interface stub only. Trunk wires the Postgres sink in a later lane."""

    def __init__(self, connection: Any = None) -> None:
        self.connection = connection

    def preflight(self) -> None:
        raise NotImplementedError(
            "DbCostSink is an interface stub; the trunk lane wires Postgres"
        )

    def emit(self, event: CostEvent) -> None:
        raise NotImplementedError(
            "DbCostSink is an interface stub; the trunk lane wires Postgres"
        )


class CostMeter:
    """Prices and emits a CostEvent for every successful model call."""

    def __init__(self, sink: CostSink | None = None) -> None:
        self.sink: CostSink = sink if sink is not None else JsonlCostSink()
        self.events: list[CostEvent] = []

    def preflight(self) -> None:
        """Let the sink fail before a billable provider call is made."""

        self.sink.preflight()

    @staticmethod
    def validate_meta(meta: dict[str, Any] | None) -> None:
        try:
            _validate_meta_dict({} if meta is None else meta)
        except (TypeError, ValueError) as exc:
            raise CostSinkError(str(exc)) from exc

    def record(
        self,
        *,
        call_site: str,
        model: str,
        in_tokens: int = 0,
        out_tokens: int = 0,
        meta: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> CostEvent:
        validate_call_site(call_site)
        meta_dict = dict(meta or {})
        self.validate_meta(meta_dict)
        price = price_for(model)
        event = CostEvent(
            call_site=call_site,
            model=model,
            in_tokens=in_tokens,
            out_tokens=out_tokens,
            unit_price=price,
            shadow_cost_usd=price.cost_usd(in_tokens, out_tokens),
            meta=meta_dict,
            timestamp=timestamp or datetime.now(UTC),
        )
        self.events.append(event)
        self.sink.emit(event)
        return event

    @property
    def total_shadow_cost_usd(self) -> float:
        return round(sum(event.shadow_cost_usd for event in self.events), 12)


def default_costs_path() -> Path:
    return Path(os.environ.get("WIKIMIND_COSTS_PATH", "costs.jsonl"))


def read_cost_events(path: str | Path | None = None) -> list[CostEvent]:
    costs_path = Path(path) if path is not None else default_costs_path()
    if not costs_path.is_file():
        return []
    try:
        text = costs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CostSinkError(f"could not read cost ledger {costs_path}: {exc}") from exc

    lines = text.splitlines()
    events: list[CostEvent] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(
                CostEvent.model_validate_json(stripped, context={"historical": True})
            )
        except ValueError as exc:
            if line_number == len(lines) and not text.endswith(("\n", "\r")):
                continue
            raise CostSinkError(
                f"invalid cost event at {costs_path}:{line_number}: {exc}"
            ) from exc
    return events


@dataclass(frozen=True, slots=True)
class CostAggregate:
    call_site: str
    calls: int
    in_tokens: int
    out_tokens: int
    shadow_cost_usd: float


def aggregate_costs(events: list[CostEvent]) -> list[CostAggregate]:
    grouped: dict[str, list[CostEvent]] = {}
    for event in events:
        grouped.setdefault(event.call_site, []).append(event)

    aggregates = [
        CostAggregate(
            call_site=call_site,
            calls=len(bucket),
            in_tokens=sum(item.in_tokens for item in bucket),
            out_tokens=sum(item.out_tokens for item in bucket),
            shadow_cost_usd=round(sum(item.shadow_cost_usd for item in bucket), 12),
        )
        for call_site, bucket in grouped.items()
    ]
    return sorted(aggregates, key=lambda item: item.call_site)


def format_usd(value: float) -> str:
    return f"${value:.12f}"


def format_cost_report(events: list[CostEvent]) -> str:
    """Render a fixed-width aggregate table, one row per call_site."""

    aggregates = aggregate_costs(events)
    if not aggregates:
        return "No cost events."

    header = (
        f"{'call_site':<24} {'calls':>7} {'in_tokens':>12} "
        f"{'out_tokens':>12} {'shadow_cost_usd':>20}"
    )
    lines = [header, "-" * len(header)]
    for item in aggregates:
        lines.append(
            f"{item.call_site:<24} {item.calls:>7} {item.in_tokens:>12} "
            f"{item.out_tokens:>12} {format_usd(item.shadow_cost_usd):>20}"
        )

    total_calls = sum(item.calls for item in aggregates)
    total_in = sum(item.in_tokens for item in aggregates)
    total_out = sum(item.out_tokens for item in aggregates)
    total_cost = round(sum(item.shadow_cost_usd for item in aggregates), 12)
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<24} {total_calls:>7} {total_in:>12} "
        f"{total_out:>12} {format_usd(total_cost):>20}"
    )
    return "\n".join(lines)
