from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wikimind.llm import (
    CostEvent,
    CostMeter,
    DbCostSink,
    InMemorySink,
    JsonlCostSink,
    aggregate_costs,
    default_costs_path,
    format_cost_report,
    format_usd,
    read_cost_events,
)
from wikimind.llm.errors import CostSinkError, PricingError
from wikimind.prices import (
    FAKE_EMBED_MODEL,
    GEMINI_2_5_FLASH_PRICE,
    GEMINI_MODEL,
    JUDGE_MODEL,
    JUDGE_SHADOW_MODEL,
    JUDGE_SHADOW_PRICE,
    ModelPrice,
    price_for,
)


def test_gemini_list_price() -> None:
    assert GEMINI_2_5_FLASH_PRICE.cost_usd(1_000_000, 1_000_000) == 2.80


def test_judge_shadow_price_is_paid_twin() -> None:
    assert price_for(JUDGE_SHADOW_MODEL) == JUDGE_SHADOW_PRICE
    assert JUDGE_SHADOW_PRICE.cost_usd(1_000_000, 1_000_000) == 2.70


def test_unknown_model_has_no_price() -> None:
    with pytest.raises(PricingError):
        price_for("unknown/model")


def test_judge_models_are_never_gemini_family() -> None:
    # V0-NOTES Ruling 10: the judge must never be a Gemini model. Guard
    # both the free endpoint and its paid twin against a future re-pin
    # that violates the family constraint.
    for judge_model in (JUDGE_MODEL, JUDGE_SHADOW_MODEL):
        assert "gemini" not in judge_model.lower()


def test_cost_meter_prices_and_emits() -> None:
    sink = InMemorySink()
    meter = CostMeter(sink)
    event = meter.record(
        call_site="l1.test",
        model=GEMINI_MODEL,
        in_tokens=1_000,
        out_tokens=500,
        meta={"arm": "B"},
    )

    assert event.shadow_cost_usd == 0.00155
    assert event.unit_price == GEMINI_2_5_FLASH_PRICE
    assert event.meta == {"arm": "B"}
    assert event.timestamp.tzinfo is not None
    assert sink.events == [event]
    assert meter.events == [event]
    assert meter.total_shadow_cost_usd == 0.00155


def test_cost_meter_prices_fake_models_at_zero() -> None:
    sink = InMemorySink()
    meter = CostMeter(sink)
    event = meter.record(
        call_site="embed.test",
        model=FAKE_EMBED_MODEL,
        in_tokens=10,
    )
    assert event.shadow_cost_usd == 0.0
    assert sink.events == [event]


def test_cost_meter_rejects_unknown_model() -> None:
    sink = InMemorySink()
    meter = CostMeter(sink)
    with pytest.raises(PricingError):
        meter.record(call_site="l1.test", model="unknown/model")
    assert sink.events == []
    assert meter.events == []


def test_cost_meter_rejects_non_json_meta() -> None:
    meter = CostMeter(InMemorySink())
    with pytest.raises(CostSinkError):
        meter.record(
            call_site="l1.test",
            model=GEMINI_MODEL,
            meta={"object": object()},
        )


def test_cost_meter_rejects_bad_call_site() -> None:
    meter = CostMeter(InMemorySink())
    with pytest.raises(ValueError):
        meter.record(call_site="ingest.test", model=GEMINI_MODEL)


def test_cost_meter_rejects_non_string_meta_keys() -> None:
    meter = CostMeter(InMemorySink())
    with pytest.raises(CostSinkError):
        meter.record(
            call_site="l1.test",
            model=GEMINI_MODEL,
            meta={1: "bad"},  # type: ignore[dict-item]
        )


def test_cost_meter_rejects_nan_meta() -> None:
    meter = CostMeter(InMemorySink())
    with pytest.raises(CostSinkError):
        meter.record(
            call_site="l1.test",
            model=GEMINI_MODEL,
            meta={"value": float("nan")},
        )


def test_cost_event_rejects_non_json_meta() -> None:
    with pytest.raises(ValueError):
        CostEvent(
            call_site="l1.test",
            model=GEMINI_MODEL,
            unit_price=GEMINI_2_5_FLASH_PRICE,
            shadow_cost_usd=0.0,
            meta={"object": object()},
        )


def test_cost_event_rejects_non_dict_meta_as_value_error() -> None:
    # A non-dict meta must surface as a pydantic ValidationError (a ValueError),
    # never a raw TypeError escaping the model.
    with pytest.raises(ValueError):
        CostEvent(
            call_site="l1.test",
            model=GEMINI_MODEL,
            unit_price=GEMINI_2_5_FLASH_PRICE,
            shadow_cost_usd=0.0,
            meta=["not", "a", "dict"],  # type: ignore[list-item]
        )


def test_cost_event_validates_tokens() -> None:
    with pytest.raises(ValueError):
        CostEvent(
            call_site="l1.test",
            model=GEMINI_MODEL,
            in_tokens=-1,
            out_tokens=0,
            unit_price=GEMINI_2_5_FLASH_PRICE,
            shadow_cost_usd=0.0,
        )


def test_cost_event_rejects_inconsistent_shadow_cost() -> None:
    with pytest.raises(ValueError):
        CostEvent(
            call_site="l1.test",
            model=GEMINI_MODEL,
            in_tokens=1_000_000,
            out_tokens=0,
            unit_price=GEMINI_2_5_FLASH_PRICE,
            shadow_cost_usd=0.0,
        )


def test_cost_event_rejects_wrong_unit_price() -> None:
    with pytest.raises(ValueError):
        CostEvent(
            call_site="judge.test",
            model=GEMINI_MODEL,
            in_tokens=0,
            out_tokens=0,
            unit_price=JUDGE_SHADOW_PRICE,
            shadow_cost_usd=0.0,
        )


def test_jsonl_sink_roundtrip(tmp_path) -> None:
    path = tmp_path / "nested" / "costs.jsonl"
    sink = JsonlCostSink(path)
    meter = CostMeter(sink)
    first = meter.record(
        call_site="qa.test",
        model=GEMINI_MODEL,
        in_tokens=100,
        out_tokens=20,
    )
    second = meter.record(
        call_site="judge.test",
        model=JUDGE_MODEL,
        in_tokens=50,
        out_tokens=10,
    )

    assert path.is_file()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    events = read_cost_events(path)
    assert [event.call_site for event in events] == ["qa.test", "judge.test"]
    assert events[0].shadow_cost_usd == first.shadow_cost_usd
    assert events[1].shadow_cost_usd == second.shadow_cost_usd


def test_jsonl_sink_appends(tmp_path) -> None:
    path = tmp_path / "costs.jsonl"
    sink = JsonlCostSink(path)
    meter = CostMeter(sink)
    meter.record(call_site="l1.a", model=GEMINI_MODEL)
    meter.record(call_site="l1.b", model=GEMINI_MODEL)
    assert len(sink.read_events()) == 2


def test_jsonl_sink_preflight_rejects_unwritable_path(tmp_path) -> None:
    sink = JsonlCostSink(tmp_path)
    with pytest.raises(CostSinkError):
        sink.preflight()


def test_jsonl_sink_redacts_key_material_from_meta(monkeypatch, tmp_path) -> None:
    key = "sk-or-v1-" + "k" * 32
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    path = tmp_path / "costs.jsonl"
    meter = CostMeter(JsonlCostSink(path))

    meter.record(
        call_site="judge.test",
        model=JUDGE_MODEL,
        in_tokens=10,
        out_tokens=2,
        meta={"note": f"echo {key}", "shape": "Bearer deadbeef"},
    )

    raw = path.read_text(encoding="utf-8")
    assert key not in raw
    assert "deadbeef" not in raw
    assert "[REDACTED]" in raw
    # The redacted line must still be a valid CostEvent on read-back.
    events = read_cost_events(path)
    assert len(events) == 1
    assert events[0].call_site == "judge.test"


def test_missing_ledger_is_empty(tmp_path) -> None:
    assert read_cost_events(tmp_path / "missing.jsonl") == []


def test_invalid_ledger_line_raises(tmp_path) -> None:
    path = tmp_path / "costs.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(CostSinkError):
        read_cost_events(path)


def test_trailing_partial_line_is_ignored(tmp_path) -> None:
    path = tmp_path / "costs.jsonl"
    sink = JsonlCostSink(path)
    meter = CostMeter(sink)
    meter.record(call_site="l1.test", model=GEMINI_MODEL)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"partial":')
    assert len(read_cost_events(path)) == 1


def test_read_cost_events_accepts_historical_price(monkeypatch, tmp_path) -> None:
    path = tmp_path / "costs.jsonl"
    meter = CostMeter(JsonlCostSink(path))
    meter.record(call_site="l1.test", model=GEMINI_MODEL, in_tokens=1000)
    monkeypatch.setattr(
        "wikimind.llm.cost.price_for",
        lambda model: ModelPrice(input_per_mtok=9.0, output_per_mtok=9.0),
    )
    events = read_cost_events(path)
    assert len(events) == 1
    assert events[0].unit_price == GEMINI_2_5_FLASH_PRICE


def test_cost_meter_retains_event_when_sink_fails() -> None:
    class FailingSink:
        def preflight(self) -> None:
            return None

        def emit(self, event: CostEvent) -> None:
            raise CostSinkError("disk full")

    meter = CostMeter(FailingSink())
    with pytest.raises(CostSinkError):
        meter.record(call_site="l1.test", model=GEMINI_MODEL)
    assert len(meter.events) == 1


def test_aggregate_and_report() -> None:
    sink = InMemorySink()
    meter = CostMeter(sink)
    meter.record(
        call_site="l1.test", model=GEMINI_MODEL, in_tokens=1000, out_tokens=100
    )
    meter.record(
        call_site="l1.test", model=GEMINI_MODEL, in_tokens=1000, out_tokens=100
    )
    meter.record(call_site="qa.test", model=GEMINI_MODEL, in_tokens=500, out_tokens=50)

    aggregates = aggregate_costs(meter.events)
    assert [item.call_site for item in aggregates] == ["l1.test", "qa.test"]
    assert aggregates[0].calls == 2
    assert aggregates[0].in_tokens == 2000

    report = format_cost_report(meter.events)
    assert "call_site" in report
    assert "l1.test" in report
    assert "qa.test" in report
    assert "TOTAL" in report
    assert format_usd(meter.total_shadow_cost_usd) in report


def test_empty_report() -> None:
    assert format_cost_report([]) == "No cost events."


def test_format_usd_is_exact() -> None:
    assert format_usd(0.000078) == "$0.000078000000"


def test_db_sink_is_a_stub() -> None:
    sink = DbCostSink()
    with pytest.raises(NotImplementedError):
        sink.emit(
            CostEvent(
                call_site="l1.test",
                model=GEMINI_MODEL,
                in_tokens=0,
                out_tokens=0,
                unit_price=GEMINI_2_5_FLASH_PRICE,
                shadow_cost_usd=0.0,
            )
        )


def test_default_costs_path_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_COSTS_PATH", str(tmp_path / "custom.jsonl"))
    assert default_costs_path() == tmp_path / "custom.jsonl"


def test_cost_event_requires_aware_timestamp() -> None:
    event = CostEvent(
        call_site="l1.test",
        model=GEMINI_MODEL,
        in_tokens=0,
        out_tokens=0,
        unit_price=GEMINI_2_5_FLASH_PRICE,
        shadow_cost_usd=0.0,
        timestamp=datetime(2026, 10, 23, 12, 0, 0),
    )
    assert event.timestamp.tzinfo == UTC
