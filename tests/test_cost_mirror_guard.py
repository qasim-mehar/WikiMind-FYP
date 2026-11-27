"""Gate C: the CLI refuses to mirror cost events into the live corpus.

Ruling 15 pins the compiled corpus to ``wikimind_live_l3``; ``costs.jsonl``
remains the append-of-record and the cost mirror must never quietly append to
``cost_events`` there.  The guard is deliberately in the entry/settings layer:
the frozen ``llm/cost.py`` meter is untouched and the check runs before any
command opens a connection.  No keys, no network, no live corpus.
"""

from __future__ import annotations

from typer.testing import CliRunner

from wikimind import cli as root_cli
from wikimind.config import (
    COST_MIRROR_ENV_VAR,
    cost_mirror_requested,
    protected_cost_mirror_database,
)
from wikimind.integrity.costs import MIRROR_ENV_VAR
from wikimind.qa import cli as qa_cli

runner = CliRunner()
LIVE_URL_DSN = "postgresql://user:secret@localhost:5432/wikimind_live_l3"
LIVE_KV_DSN = "host=localhost dbname=wikimind_live_l3 user=user"
SAFE_DSN = "postgresql://user:secret@localhost:5432/wikimind_l0"


def test_guard_env_var_matches_the_frozen_sink_constant() -> None:
    """One name, declared in settings, must agree with the mirror implementation."""

    assert COST_MIRROR_ENV_VAR == MIRROR_ENV_VAR == "WIKIMIND_COST_DB_MIRROR"


def test_mirror_is_armed_only_by_the_sink_activation_value() -> None:
    assert cost_mirror_requested({COST_MIRROR_ENV_VAR: "1"}) is True
    assert cost_mirror_requested({COST_MIRROR_ENV_VAR: "0"}) is False
    assert cost_mirror_requested({}) is False


def test_only_the_live_corpus_is_a_protected_mirror_target() -> None:
    assert protected_cost_mirror_database(LIVE_URL_DSN) == "wikimind_live_l3"
    assert protected_cost_mirror_database(LIVE_KV_DSN) == "wikimind_live_l3"
    assert protected_cost_mirror_database(SAFE_DSN) is None
    assert protected_cost_mirror_database("not a dsn") is None


def test_cli_refuses_mirror_into_live_corpus(monkeypatch) -> None:
    monkeypatch.setenv("WIKIMIND_DB", LIVE_URL_DSN)
    monkeypatch.setenv(COST_MIRROR_ENV_VAR, "1")

    result = runner.invoke(root_cli.app, ["ask", "a golden question?"])

    assert result.exit_code == 2, result.output
    assert "protected corpus database 'wikimind_live_l3'" in result.output
    assert "--force" in result.output
    # The guard fires before a connection is ever attempted.
    assert "reached ask" not in result.output


def test_cli_force_override_reaches_the_command(monkeypatch) -> None:
    monkeypatch.setenv("WIKIMIND_DB", LIVE_URL_DSN)
    monkeypatch.setenv(COST_MIRROR_ENV_VAR, "1")

    def boom(_dsn):
        raise RuntimeError("reached ask")

    monkeypatch.setattr(qa_cli, "_connect", boom)
    result = runner.invoke(root_cli.app, ["--force", "ask", "a golden question?"])

    assert result.exit_code == 2, result.output
    assert "error: reached ask" in result.output
    assert "protected corpus" not in result.output


def test_cli_allows_mirror_into_a_regular_database(monkeypatch) -> None:
    monkeypatch.setenv("WIKIMIND_DB", SAFE_DSN)
    monkeypatch.setenv(COST_MIRROR_ENV_VAR, "1")

    def boom(_dsn):
        raise RuntimeError("reached ask")

    monkeypatch.setattr(qa_cli, "_connect", boom)
    result = runner.invoke(root_cli.app, ["ask", "a golden question?"])

    assert result.exit_code == 2, result.output
    assert "error: reached ask" in result.output
    assert "protected corpus" not in result.output
