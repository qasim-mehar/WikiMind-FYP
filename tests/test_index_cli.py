"""The ``wikimind index`` CLI: mounting, embedding run, idempotent re-run, errors."""

from __future__ import annotations

import re
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from index_seed import seed_node, seed_node_version, seed_project
from typer.testing import CliRunner

from wikimind.cli import app as root_app
from wikimind.index import INDEX_CALL_SITE
from wikimind.index import cli as index_cli
from wikimind.llm import FakeEmbedder
from wikimind.llm.cost import read_cost_events
from wikimind.prices import FAKE_EMBED_MODEL

# COLUMNS forces a deterministic rich console width, matching test_llm_cli.
runner = CliRunner(env={"COLUMNS": "200"})
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain(output: str) -> str:
    return _ANSI_RE.sub("", output)


def _fake_embedder(meter):
    return FakeEmbedder(meter=meter, call_site=INDEX_CALL_SITE)


@pytest.fixture
def index_conn(test_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        yield conn


def test_root_cli_mounts_index_command() -> None:
    result = runner.invoke(root_app, ["--help"])

    assert result.exit_code == 0
    assert "index" in plain(result.output)


def test_index_cli_embeds_once_then_skips(index_conn, test_dsn, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(index_cli, "_build_embedder", _fake_embedder)
    ledger = tmp_path / "costs.jsonl"
    name = f"idx-cli-{uuid4().hex[:12]}"
    project_id = seed_project(index_conn, name)
    node_id = seed_node(index_conn, project_id, "alpha")
    seed_node_version(index_conn, project_id, node_id, "alpha page")

    argv = ["index", name, "--dsn", test_dsn, "--costs-path", str(ledger)]
    first = runner.invoke(root_app, argv)
    second = runner.invoke(root_app, argv)

    assert first.exit_code == 0, first.output
    output = plain(first.output)
    assert "1 embedded" in output
    assert "0 skipped" in output
    assert f"model: {FAKE_EMBED_MODEL}" in output
    assert "shadow cost: $0.000000000000" in output

    assert second.exit_code == 0, second.output
    assert "0 embedded, 1 skipped" in plain(second.output)

    events = read_cost_events(ledger)
    # The idempotent second run embeds nothing, so only one call is metered.
    assert [event.call_site for event in events] == [INDEX_CALL_SITE]
    assert all(event.shadow_cost_usd == 0.0 for event in events)


def test_index_cli_unknown_project_exits_two(test_dsn, monkeypatch) -> None:
    monkeypatch.setattr(index_cli, "_build_embedder", _fake_embedder)

    result = runner.invoke(root_app, ["index", "no-such-project", "--dsn", test_dsn])

    assert result.exit_code == 2
    assert "error:" in plain(result.output)


def test_index_cli_ambiguous_name_exits_two(index_conn, test_dsn, monkeypatch) -> None:
    monkeypatch.setattr(index_cli, "_build_embedder", _fake_embedder)
    name = f"dup-{uuid4().hex[:12]}"
    seed_project(index_conn, name)
    seed_project(index_conn, name)

    result = runner.invoke(root_app, ["index", name, "--dsn", test_dsn])

    assert result.exit_code == 2
    assert "ambiguous" in plain(result.output)


def test_index_cli_without_dsn_exits_two(monkeypatch) -> None:
    monkeypatch.setattr(index_cli, "_build_embedder", _fake_embedder)

    result = runner.invoke(root_app, ["index", "whatever"], env={"WIKIMIND_DB": ""})

    assert result.exit_code == 2
    assert "no database DSN configured" in plain(result.output)
