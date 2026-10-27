from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from wikimind.llm import CostMeter, JsonlCostSink, LLMResult, Usage, cli
from wikimind.llm.cli import app
from wikimind.prices import EMBED_DIM, EMBED_MODEL, GEMINI_MODEL, JUDGE_MODEL

# COLUMNS forces a deterministic rich console width so option rendering does
# not vary with the host terminal.
runner = CliRunner(env={"COLUMNS": "200"})

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain_help(output: str) -> str:
    """Strip styling and whitespace so assertions match option names, not layout.

    typer forces color rendering when GITHUB_ACTIONS is set, and its option
    highlighter emits escape codes inside option tokens; narrow consoles also
    soft-wrap them. Raw substring matching would depend on both.
    """
    return re.sub(r"\s+", "", _ANSI_RE.sub("", output))


class StubGemini:
    def __init__(self, *, api_key: str, meter: CostMeter) -> None:
        self.meter = meter

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: Any,
        call_site: str,
        max_retries: int = 3,
        meta: dict[str, Any] | None = None,
    ) -> LLMResult:
        self.meter.record(
            call_site=call_site,
            model=GEMINI_MODEL,
            in_tokens=10,
            out_tokens=5,
            meta=meta or {},
        )
        return LLMResult(json={"ok": True, "summary": "stub"}, usage=Usage(10, 5))


class StubJudge:
    def __init__(self, *, api_key: str, meter: CostMeter) -> None:
        self.meter = meter

    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: Any,
        call_site: str,
        max_retries: int = 3,
        meta: dict[str, Any] | None = None,
    ) -> LLMResult:
        self.meter.record(
            call_site=call_site,
            model=JUDGE_MODEL,
            in_tokens=20,
            out_tokens=4,
            meta=meta or {},
        )
        return LLMResult(json={"score": 5, "reason": "stub"}, usage=Usage(20, 4))


class StubEmbedder:
    def __init__(self, *, meter: CostMeter, call_site: str) -> None:
        self.meter = meter
        self.call_site = call_site

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.meter.record(
            call_site=self.call_site,
            model=EMBED_MODEL,
            in_tokens=3,
            out_tokens=0,
            meta={"texts": len(texts)},
        )
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]


class FailingGemini:
    def __init__(self, *, api_key: str, meter: CostMeter) -> None:
        self.api_key = api_key

    def complete(self, **kwargs: Any) -> LLMResult:
        raise RuntimeError(f"leaked credential {self.api_key}")


def test_smoke_is_dry_without_live_flag(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WIKIMIND_LIVE", raising=False)
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    ledger = tmp_path / "costs.jsonl"

    result = runner.invoke(app, ["llm", "smoke", "--costs-path", str(ledger)])

    assert result.exit_code == 0
    assert "dry run" in result.output.lower()
    # The hardcoded literal below is deliberate: an ungoverned repin of the
    # drafter fails CI right here. Update it only together with prices.py and
    # the governing ruling; see V0-NOTES Rulings 12/13.
    assert "gemini-3.1-flash-lite" in result.output
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
    assert "$0.000400000000" in result.output
    assert "$0.000200000000" in result.output
    assert "$0.000600000000" in result.output


def test_costs_missing_ledger(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    result = runner.invoke(app, ["llm", "costs", "--path", str(tmp_path / "none.jsonl")])
    assert result.exit_code == 0
    assert "No cost events" in result.output


def test_costs_corrupt_ledger_is_a_friendly_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    ledger = tmp_path / "corrupt.jsonl"
    ledger.write_text('{"call_site": "l1.test", "broken"\n', encoding="utf-8")

    result = runner.invoke(app, ["llm", "costs", "--path", str(ledger)])

    assert result.exit_code == 1
    assert "Could not read cost ledger" in result.output
    assert "corrupt.jsonl" in result.output


def test_llm_help() -> None:
    result = runner.invoke(app, ["llm", "--help"])
    assert result.exit_code == 0
    assert "smoke" in result.output
    assert "costs" in result.output


def test_smoke_help() -> None:
    result = runner.invoke(app, ["llm", "smoke", "--help"])
    assert result.exit_code == 0
    assert "costs-path" in plain_help(result.output)


def test_costs_help() -> None:
    result = runner.invoke(app, ["llm", "costs", "--help"])
    assert result.exit_code == 0
    assert "--path" in plain_help(result.output)


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


def test_costs_loads_dotenv(monkeypatch, tmp_path) -> None:
    ledger = tmp_path / "dotenv.jsonl"
    meter = CostMeter(JsonlCostSink(ledger))
    meter.record(call_site="l1.dotenv", model=GEMINI_MODEL, in_tokens=1000, out_tokens=100)
    env_file = tmp_path / "custom.env"
    env_file.write_text(f"WIKIMIND_COSTS_PATH={ledger}\n", encoding="utf-8")
    monkeypatch.delenv("WIKIMIND_COSTS_PATH", raising=False)
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(env_file))

    result = runner.invoke(app, ["llm", "costs"])

    assert result.exit_code == 0
    assert "l1.dotenv" in result.output


def test_live_smoke_success(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_LIVE", "1")
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    ledger = tmp_path / "live.jsonl"
    monkeypatch.setattr(cli, "GeminiClient", StubGemini)
    monkeypatch.setattr(cli, "JudgeClient", StubJudge)
    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", StubEmbedder)

    result = runner.invoke(app, ["llm", "smoke", "--costs-path", str(ledger)])

    assert result.exit_code == 0
    assert "gemini: OK" in result.output
    assert "judge: OK" in result.output
    assert "embed: OK 3 vectors" in result.output
    assert "Cost report (this session):" in result.output
    assert "Total session shadow cost" in result.output
    assert "smoke: OK" in result.output
    assert ledger.is_file()


def test_live_smoke_partial_failure_redacts_and_keeps_going(monkeypatch, tmp_path) -> None:
    key = "AIza" + "S" * 30
    monkeypatch.setenv("WIKIMIND_LIVE", "1")
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("GEMINI_API_KEY", key)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    ledger = tmp_path / "live-partial.jsonl"
    monkeypatch.setattr(cli, "GeminiClient", FailingGemini)
    monkeypatch.setattr(cli, "JudgeClient", StubJudge)
    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", StubEmbedder)

    result = runner.invoke(app, ["llm", "smoke", "--costs-path", str(ledger)])

    assert result.exit_code == 1
    assert "gemini: FAILED" in result.output
    assert "smoke: FAILED (gemini)" in result.output
    assert key not in result.output
    assert "[REDACTED]" in result.output
    assert "judge: OK" in result.output
    assert "embed: OK 3 vectors" in result.output
    assert "Cost report (this session):" in result.output
    assert "Total session shadow cost" in result.output
    assert ledger.is_file()


class FailingJudge:
    def __init__(self, *, api_key: str, meter: CostMeter) -> None:
        pass

    def complete(self, **kwargs: Any) -> LLMResult:
        raise RuntimeError("judge is down")


def test_smoke_failed_legs_are_listed_in_banner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WIKIMIND_LIVE", "1")
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    ledger = tmp_path / "live-multi.jsonl"
    monkeypatch.setattr(cli, "GeminiClient", FailingGemini)
    monkeypatch.setattr(cli, "JudgeClient", FailingJudge)
    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", StubEmbedder)

    result = runner.invoke(app, ["llm", "smoke", "--costs-path", str(ledger)])

    assert result.exit_code == 1
    assert "gemini: FAILED" in result.output
    assert "judge: FAILED" in result.output
    assert "embed: OK 3 vectors" in result.output
    # Status lines come first; the cost table still prints; banner is last.
    assert result.output.index("gemini: FAILED") < result.output.index(
        "Cost report (this session):"
    )
    assert "Cost report (this session):" in result.output
    assert result.output.rstrip().endswith("smoke: FAILED (gemini, judge)")
    assert ledger.is_file()
