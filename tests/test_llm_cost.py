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
    GEMINI_2_5_FLASH_PRICE,
    GEMINI_MODEL,
    JUDGE_SHADOW_MODEL,
    JUDGE_SHADOW_PRICE,
    LOCAL_EMBED_PRICE,
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


def test_cost_meter_accepts_explicit_zero_price() -> None:
    sink = InMemorySink()
    meter = CostMeter(sink)
    event = meter.record(
        call_site="embed.test",
        model="fake-embedder",
        in_tokens=10,
        unit_price=LOCAL_EMBED_PRICE,
    )
    assert event.shadow_cost_usd == 0.0


def test_cost_meter_rejects_bad_call_site() -> None:
    meter = CostMeter(InMemorySink())
    with pytest.raises(ValueError):
        meter.record(call_site="ingest.test", model=GEMINI_MODEL)


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
        model="nvidia/nemotron-3-super-120b-a12b:free",
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


def test_missing_ledger_is_empty(tmp_path) -> None:
    assert read_cost_events(tmp_path / "missing.jsonl") == []


def test_invalid_ledger_line_raises(tmp_path) -> None:
    path = tmp_path / "costs.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(CostSinkError):
        read_cost_events(path)


def test_aggregate_and_report() -> None:
    sink = InMemorySink()
    meter = CostMeter(sink)
    meter.record(call_site="l1.test", model=GEMINI_MODEL, in_tokens=1000, out_tokens=100)
    meter.record(call_site="l1.test", model=GEMINI_MODEL, in_tokens=1000, out_tokens=100)
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
