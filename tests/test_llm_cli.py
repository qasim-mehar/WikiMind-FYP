from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wikimind.llm import CostMeter, JsonlCostSink
from wikimind.llm.cli import app
from wikimind.prices import GEMINI_MODEL

runner = CliRunner()


def test_smoke_is_dry_without_live_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WIKIMIND_LIVE", raising=False)
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    ledger = tmp_path / "costs.jsonl"

    result = runner.invoke(app, ["llm", "smoke", "--costs-path", str(ledger)])

    assert result.exit_code == 0
    assert "dry run" in result.output.lower()
    assert "gemini-2.5-flash" in result.output
    assert "judge.smoke" in result.output
    assert not ledger.exists()


def test_smoke_reports_missing_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_LIVE", "1")
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    ledger = tmp_path / "costs.jsonl"

    result = runner.invoke(app, ["llm", "smoke", "--costs-path", str(ledger)])

    assert result.exit_code == 0
    assert "Missing keys" in result.output
    assert "dry run" in result.output.lower()
    assert not ledger.exists()


def test_costs_aggregates_ledger(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    ledger = tmp_path / "costs.jsonl"
    meter = CostMeter(JsonlCostSink(ledger))
    meter.record(call_site="l1.test", model=GEMINI_MODEL, in_tokens=1000, out_tokens=100)
    meter.record(call_site="qa.test", model=GEMINI_MODEL, in_tokens=500, out_tokens=50)

    result = runner.invoke(app, ["llm", "costs", "--path", str(ledger)])

    assert result.exit_code == 0
    assert "l1.test" in result.output
    assert "qa.test" in result.output
    assert "TOTAL" in result.output
    assert "$0.000550000000" in result.output


def test_costs_missing_ledger(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    result = runner.invoke(app, ["llm", "costs", "--path", str(tmp_path / "none.jsonl")])
    assert result.exit_code == 0
    assert "No cost events" in result.output


def test_llm_help() -> None:
    result = runner.invoke(app, ["llm", "--help"])
    assert result.exit_code == 0
    assert "smoke" in result.output
    assert "costs" in result.output


def test_smoke_help() -> None:
    result = runner.invoke(app, ["llm", "smoke", "--help"])
    assert result.exit_code == 0
    assert "costs-path" in result.output


def test_costs_help() -> None:
    result = runner.invoke(app, ["llm", "costs", "--help"])
    assert result.exit_code == 0
    assert "--path" in result.output


def test_default_path_is_used(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    ledger = tmp_path / "default.jsonl"
    monkeypatch.setenv("WIKIMIND_COSTS_PATH", str(ledger))
    meter = CostMeter(JsonlCostSink(ledger))
    meter.record(call_site="judge.test", model=GEMINI_MODEL, in_tokens=10, out_tokens=2)

    result = runner.invoke(app, ["llm", "costs"])
    assert result.exit_code == 0
    assert "judge.test" in result.output
    assert Path(ledger).is_file()
