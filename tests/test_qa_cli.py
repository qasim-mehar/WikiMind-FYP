"""CLI surface tests for ``wikimind ask`` (no live keys, no live corpus)."""

from __future__ import annotations

import re
from types import SimpleNamespace
from uuid import uuid4

from typer.testing import CliRunner

from wikimind.cli import app as root_app
from wikimind.qa import cli as qa_cli
from wikimind.qa.pipeline import AnswerResult
from wikimind.qa.prompts import AnswerCitation

runner = CliRunner(env={"COLUMNS": "200"})
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain(output: str) -> str:
    return re.sub(r"\s+", "", _ANSI_RE.sub("", output))


def test_ask_help() -> None:
    result = runner.invoke(root_app, ["ask", "--help"])
    assert result.exit_code == 0
    text = plain(result.output)
    for option in (
        "--project",
        "--dsn",
        "--costs-path",
        "--top-k",
        "--hops",
        "--max-claims",
        "--max-attempts",
    ):
        assert option in text


def test_ask_requires_dsn(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WIKIMIND_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("WIKIMIND_DB", raising=False)
    result = runner.invoke(root_app, ["ask", "what is the answer?"])
    assert result.exit_code == 2
    assert "no database DSN" in result.output


def test_ask_reports_connection_errors_without_traceback(monkeypatch) -> None:
    def boom(_dsn):
        raise RuntimeError("connection exploded")

    monkeypatch.setattr(qa_cli, "_connect", boom)
    result = runner.invoke(root_app, ["ask", "question", "--dsn", "postgresql://x"])
    assert result.exit_code == 2
    assert "error: connection exploded" in result.output
    assert "Traceback" not in result.output


def test_run_ask_prints_answer_citations_cost_and_wall_time(monkeypatch) -> None:
    claim_id = uuid4()

    class FakeConn:
        def close(self) -> None:
            pass

    monkeypatch.setattr(qa_cli, "_connect", lambda _dsn: FakeConn())
    monkeypatch.setattr(
        qa_cli, "_resolve_project_ref", lambda _conn, _ref: SimpleNamespace(id=uuid4())
    )
    from wikimind.qa.pipeline import ClaimRecord

    result = AnswerResult(
        answer="The Transformer uses attention [c-1].",
        sufficient=True,
        citations=(AnswerCitation(claim_id="c-1", document_slug="source:abc"),),
        claims=(
            ClaimRecord(
                ref="c-1",
                claim_id=claim_id,
                text="The Transformer uses attention.",
                document_slug="source:abc",
            ),
        ),
        shadow_cost_usd=0.000012,
        ledger_events=2,
        wall_time_s=0.42,
        seeds=(uuid4(),),
        claim_cap=40,
        truncated=False,
    )
    monkeypatch.setattr(qa_cli, "answer_question", lambda *args, **kwargs: result)

    invoked = runner.invoke(root_app, ["ask", "What architecture?", "--dsn", "postgresql://x"])

    assert invoked.exit_code == 0, invoked.output
    assert "answer:   The Transformer uses attention [c-1]." in invoked.output
    assert "sufficient: true" in invoked.output
    assert "[c-1] source:abc — The Transformer uses attention." in invoked.output
    assert "shadow:" in invoked.output and "wall:" in invoked.output


def test_print_result_contains_all_sections(capsys) -> None:
    claim_id = uuid4()
    qa_cli._print_result(
        AnswerResult(
            answer="Answer text [c-1].",
            sufficient=False,
            citations=(AnswerCitation(claim_id="c-1", document_slug="source:abc"),),
            claims=(
                SimpleNamespace(
                    ref="c-1", claim_id=claim_id, text="Source claim", document_slug="source:abc"
                ),
            ),
            shadow_cost_usd=0.000001,
            ledger_events=2,
            wall_time_s=0.1,
            seeds=(),
            claim_cap=40,
            truncated=False,
        ),
        "a question",
    )
    out = capsys.readouterr().out
    assert "question: a question" in out
    assert "answer:   Answer text [c-1]." in out
    assert "sufficient: false" in out
    assert "[c-1] source:abc — Source claim" in out
    assert "shadow:" in out
    assert "wall:" in out
