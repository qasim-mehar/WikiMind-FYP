"""Node-version materialization tests (Governance Ruling 14, lane 5).

Render determinism is pinned with content digests in the FakeEmbedder style:
``rendered_text`` is a pure function of stored rows, so the same graph state
must always produce byte-identical text, on the compile path and on backfill.

DB tests use the conftest template protocol; provider calls are scripted
fakes (zero LLM cost — rendering and backfill are local).
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import psycopg
from index_seed import seed_node, seed_node_version, seed_project
from psycopg.rows import dict_row
from typer.testing import CliRunner

from wikimind.cli import app as root_app
from wikimind.compile import compile_project, register_document, resolve_project
from wikimind.compile.graph import create_project
from wikimind.llm import LLMResult, Usage
from wikimind.parse.models import ChannelBuilder, make_document
from wikimind.render import render_node_text
from wikimind.render.versions import backfill_project, node_claim_texts, render_node


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


# --------------------------------------------------------------------------
# helpers (mirroring the compile-pipeline test script shape)


@dataclass
class ScriptedLLM:
    """Routes scripted responses by call_site; records every call."""

    responses: dict[str, list[Any]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def queue(self, call_site: str, *items: Any) -> None:
        self.responses.setdefault(call_site, []).extend(items)

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
        self.calls.append({"call_site": call_site, "user": user, "meta": dict(meta or {})})
        queue = deque(self.responses.setdefault(call_site, []))
        if not queue:
            raise AssertionError(f"no scripted response queued for {call_site}")
        item = queue.popleft()
        self.responses[call_site] = list(queue)
        if isinstance(item, BaseException):
            raise item
        return LLMResult(json=item, usage=Usage(17, 9))


def _register(conn: psycopg.Connection, project_id: UUID, filename: str, text: str) -> UUID:
    builder = ChannelBuilder()
    builder.add("section", text)
    parsed = make_document(
        parser="text",
        source_filename=filename,
        data=text.encode("utf-8"),
        channels=builder.finish(),
    )
    document_id, _ = register_document(conn, project_id, parsed, source_uri=f"memo://{filename}")
    return document_id


def _one(text: str, quote: str, entities: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "claims": [
            {
                "claim_id_hint": "c1",
                "text": text,
                "support_quote": quote,
                "entities": entities,
                "relations": [],
                "confidence": "high",
                "notes": "",
            }
        ]
    }


def _write(text: str, entities: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "disposition": "new",
        "target_claim_id": None,
        "text": text,
        "entities": entities,
        "relations": [],
        "notes": "",
    }


def _judge() -> dict[str, Any]:
    return {"entailed": True, "score": 5, "reason": "quoted passage."}


def _script_claim(drafter: ScriptedLLM, judge: ScriptedLLM, text: str) -> None:
    drafter.queue("compile.analyze", _one(text, text, [{"name": "Alpha", "type": "person"}]))
    drafter.queue("compile.write", _write(text, [{"name": "Alpha", "type": "person"}]))
    judge.queue("compile.judge", _judge())


CLAIM_1 = "Alpha joined Beta Labs in 2017."
CLAIM_2 = "Alpha leads the Delta team."
PASSAGE_A = CLAIM_1 + " Alpha previously worked at Gamma."
PASSAGE_B = CLAIM_2 + " The Delta team grew in 2018."

# Pinned digests: rendered text is a pure function of (slug, type, claim
# texts), so these must never move without a documented format change.
ENTITY_V1_TEXT = f"alpha [entity]\n\n- {CLAIM_1}"
ENTITY_V2_TEXT = f"alpha [entity]\n\n- {CLAIM_1}\n\n- {CLAIM_2}"
ENTITY_V1_DIGEST = "d4820d0958ce3f8d0f83f4cc8a61300458d8ae0c8ef0fa760abadcdb60b1371d"
ENTITY_V2_DIGEST = "5c2cd03198b021c5f8ac2110fd877f475f784ac69c65f64ba7b6ce41eb90bac5"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# pure rendering


def test_render_node_text_is_deterministic_and_pinned() -> None:
    claims = [(uuid4(), CLAIM_1), (uuid4(), CLAIM_2)]
    first = render_node_text("alpha", "entity", claims)
    # Input order and claim ids must not matter: same texts, same bytes.
    second = render_node_text("alpha", "entity", list(reversed(claims)))
    assert first == second == ENTITY_V2_TEXT
    assert _digest(first) == ENTITY_V2_DIGEST


def test_render_node_text_single_claim_pinned() -> None:
    text = render_node_text("alpha", "entity", [(uuid4(), CLAIM_1)])
    assert text == ENTITY_V1_TEXT
    assert _digest(text) == ENTITY_V1_DIGEST


def test_render_node_text_without_claims_is_just_the_header() -> None:
    assert render_node_text("source:abc", "citation", []) == "source:abc [citation]"


# --------------------------------------------------------------------------
# N1: creation path — version 1 in the same transaction as the node


def test_compile_writes_version_one_for_every_node(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project = create_project(conn, f"lane5-{uuid4().hex[:12]}")
    project_id = project.id
    _register(conn, project_id, "doc_a.txt", PASSAGE_A)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _script_claim(drafter, judge, CLAIM_1)

    result = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter, judge=judge
    )
    assert result.status == "done"
    assert result.stats.node_versions == 2  # citation node + entity node

    nodes = conn.execute(
        "SELECT * FROM nodes WHERE project_id = %s ORDER BY slug", (project_id,)
    ).fetchall()
    assert len(nodes) == 2
    for node in nodes:
        assert node["current_version_id"] is not None
        versions = conn.execute(
            """
            SELECT * FROM node_versions WHERE node_id = %s ORDER BY version
            """,
            (node["id"],),
        ).fetchall()
        assert [v["version"] for v in versions] == [1]
        assert versions[0]["id"] == node["current_version_id"]
        assert versions[0]["rendered_text"] == render_node(conn, project_id, node["id"])

    # Entity node: header plus the one claim, pinned digest.
    entity = next(node for node in nodes if node["slug"] == "alpha")
    entity_v1 = conn.execute(
        "SELECT rendered_text FROM node_versions WHERE id = %s",
        (entity["current_version_id"],),
    ).fetchone()["rendered_text"]
    assert entity_v1 == ENTITY_V1_TEXT
    assert _digest(entity_v1) == ENTITY_V1_DIGEST
    conn.close()


def test_empty_commit_still_versions_the_citation_node(test_dsn: str) -> None:
    """An all-rejected unit creates the citation node; it must get version 1."""

    conn = _connect(test_dsn)
    project = create_project(conn, f"lane5-{uuid4().hex[:12]}")
    project_id = project.id
    _register(conn, project_id, "doc_a.txt", PASSAGE_A)
    bad = {"claims": [{"nope": True}]}
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    drafter.queue("compile.analyze", bad, bad)

    result = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter, judge=judge
    )
    assert result.status == "done"

    # The schema-reject path discards commit counts (pre-existing pipeline
    # behavior); assert the materialization itself in the database.
    node = conn.execute("SELECT * FROM nodes WHERE project_id = %s", (project_id,)).fetchone()
    assert node["current_version_id"] is not None
    version = conn.execute(
        "SELECT * FROM node_versions WHERE id = %s", (node["current_version_id"],)
    ).fetchone()
    assert version["version"] == 1
    assert version["rendered_text"] == (f"source:{_register_sha(PASSAGE_A)} [citation]")
    conn.close()


def _register_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# N2: append path — a later unit's commit appends version n+1


def test_later_unit_appends_version_and_advances_current(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project = create_project(conn, f"lane5-{uuid4().hex[:12]}")
    project_id = project.id

    # Unit 1 commits the first claim about Alpha.
    _register(conn, project_id, "doc_a.txt", PASSAGE_A)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _script_claim(drafter, judge, CLAIM_1)
    first = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter, judge=judge
    )
    assert first.status == "done"

    entity = conn.execute(
        "SELECT * FROM nodes WHERE project_id = %s AND slug = 'alpha'", (project_id,)
    ).fetchone()
    v1 = conn.execute(
        "SELECT * FROM node_versions WHERE id = %s", (entity["current_version_id"],)
    ).fetchone()
    assert v1["version"] == 1
    v1_text = v1["rendered_text"]
    assert v1_text == ENTITY_V1_TEXT

    # Unit 2 (a later compile run) commits a second claim about Alpha.
    _register(conn, project_id, "doc_b.txt", PASSAGE_B)
    drafter2, judge2 = ScriptedLLM(), ScriptedLLM()
    _script_claim(drafter2, judge2, CLAIM_2)
    second = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter2, judge=judge2
    )
    assert second.status == "done"

    entity = conn.execute(
        "SELECT * FROM nodes WHERE project_id = %s AND slug = 'alpha'", (project_id,)
    ).fetchone()
    versions = conn.execute(
        "SELECT * FROM node_versions WHERE node_id = %s ORDER BY version", (entity["id"],)
    ).fetchall()
    # Append-only: exactly two rows, nothing rewritten.
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["rendered_text"] == v1_text
    assert versions[1]["rendered_text"] == ENTITY_V2_TEXT
    assert _digest(versions[1]["rendered_text"]) == ENTITY_V2_DIGEST
    assert entity["current_version_id"] == versions[1]["id"]

    # The claim connection is bidirectional-provenance: both claims render.
    connected = {text for _, text in node_claim_texts(conn, project_id, entity["id"])}
    assert connected == {CLAIM_1, CLAIM_2}
    conn.close()


def test_no_committed_node_has_null_current_version_id(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project = create_project(conn, f"lane5-{uuid4().hex[:12]}")
    project_id = project.id
    project = resolve_project(conn, str(project_id))
    # Two sequential compile runs: document ids are random, so unit order
    # within one run is not fixed; compiling one document at a time keeps
    # the scripted responses aligned with their units.
    _register(conn, project_id, "doc_a.txt", PASSAGE_A)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _script_claim(drafter, judge, CLAIM_1)
    assert compile_project(conn, project=project, drafter=drafter, judge=judge).status == "done"

    _register(conn, project_id, "doc_b.txt", PASSAGE_B)
    drafter2, judge2 = ScriptedLLM(), ScriptedLLM()
    _script_claim(drafter2, judge2, CLAIM_2)
    result = compile_project(conn, project=project, drafter=drafter2, judge=judge2)
    assert result.status == "done"

    nulls = conn.execute(
        """
        SELECT count(*) AS n FROM nodes
        WHERE project_id = %s AND current_version_id IS NULL
        """,
        (project_id,),
    ).fetchone()["n"]
    assert nulls == 0
    conn.close()


# --------------------------------------------------------------------------
# N3: backfill


def _strip_versions(conn: psycopg.Connection, project_id: UUID) -> None:
    """Simulate a pre-Ruling-14 corpus: compiled, but versionless."""

    conn.execute("UPDATE nodes SET current_version_id = NULL WHERE project_id = %s", (project_id,))
    conn.execute(
        """
        DELETE FROM node_versions v USING nodes n
        WHERE v.node_id = n.id AND n.project_id = %s
        """,
        (project_id,),
    )


def test_backfill_versions_multi_unit_corpus_idempotently(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project = create_project(conn, f"lane5-{uuid4().hex[:12]}")
    project_id, name = project.id, project.name
    project = resolve_project(conn, str(project_id))
    # Two units across two documents (compiled sequentially; see the
    # ordering note in the null-invariant test).
    _register(conn, project_id, "doc_a.txt", PASSAGE_A)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _script_claim(drafter, judge, CLAIM_1)
    assert compile_project(conn, project=project, drafter=drafter, judge=judge).status == "done"

    _register(conn, project_id, "doc_b.txt", PASSAGE_B)
    drafter2, judge2 = ScriptedLLM(), ScriptedLLM()
    _script_claim(drafter2, judge2, CLAIM_2)
    assert compile_project(conn, project=project, drafter=drafter2, judge=judge2).status == "done"

    node_count = conn.execute(
        "SELECT count(*) AS n FROM nodes WHERE project_id = %s", (project_id,)
    ).fetchone()["n"]
    assert node_count == 3  # two citation nodes + alpha

    _strip_versions(conn, project_id)

    # Backfill through the CLI (mounts as `wikimind render backfill`).
    runner = CliRunner()
    argv = ["render", "backfill", name, "--dsn", test_dsn]
    first = runner.invoke(root_app, argv)
    assert first.exit_code == 0, first.output
    assert "3 total, 3 versioned, 0 already versioned" in first.output

    rows = conn.execute(
        """
        SELECT n.slug, n.current_version_id, v.version, v.rendered_text
        FROM nodes n JOIN node_versions v ON v.id = n.current_version_id
        WHERE n.project_id = %s
        ORDER BY n.slug
        """,
        (project_id,),
    ).fetchall()
    assert len(rows) == 3
    for row in rows:
        assert row["version"] == 1
    by_slug = {row["slug"]: row["rendered_text"] for row in rows}
    assert by_slug["alpha"] == ENTITY_V2_TEXT  # both claims, rendered from rows
    assert by_slug[f"source:{_register_sha(PASSAGE_A)}"] == (
        f"source:{_register_sha(PASSAGE_A)} [citation]\n\n- {CLAIM_1}"
    )
    assert by_slug[f"source:{_register_sha(PASSAGE_B)}"] == (
        f"source:{_register_sha(PASSAGE_B)} [citation]\n\n- {CLAIM_2}"
    )
    assert _digest(by_slug["alpha"]) == ENTITY_V2_DIGEST

    snapshot = conn.execute(
        """
        SELECT v.node_id, v.version, v.rendered_text, n.current_version_id
        FROM node_versions v JOIN nodes n ON n.current_version_id = v.id
        WHERE n.project_id = %s
        ORDER BY v.node_id, v.version
        """,
        (project_id,),
    ).fetchall()

    # Idempotent: the second run writes nothing.
    second = runner.invoke(root_app, argv)
    assert second.exit_code == 0, second.output
    assert "3 total, 0 versioned, 3 already versioned" in second.output
    after = conn.execute(
        """
        SELECT v.node_id, v.version, v.rendered_text, n.current_version_id
        FROM node_versions v JOIN nodes n ON n.current_version_id = v.id
        WHERE n.project_id = %s
        ORDER BY v.node_id, v.version
        """,
        (project_id,),
    ).fetchall()
    assert after == snapshot

    # The invariant holds on the backfilled corpus too.
    nulls = conn.execute(
        """
        SELECT count(*) AS n FROM nodes
        WHERE project_id = %s AND current_version_id IS NULL
        """,
        (project_id,),
    ).fetchone()["n"]
    assert nulls == 0
    conn.close()


def test_backfill_leaves_versioned_nodes_alone(test_dsn: str) -> None:
    # Plain tuple connection: the seed helpers index rows positionally.
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        project_id = seed_project(conn, f"lane5-{uuid4().hex[:12]}")
        seed_node_version(
            conn,
            project_id,
            seed_node(conn, project_id, "alpha"),
            "alpha [entity]\n\n- hand-written history stays",
        )
        seed_node(conn, project_id, "beta")  # no version

        result = backfill_project(conn, project_id)
        assert result.nodes_total == 2
        assert result.versioned == 1

        versions = conn.execute(
            """
            SELECT v.rendered_text FROM node_versions v
            JOIN nodes n ON n.id = v.node_id
            WHERE n.project_id = %s AND n.slug = 'alpha'
            """,
            (project_id,),
        ).fetchall()
        assert [v[0] for v in versions] == ["alpha [entity]\n\n- hand-written history stays"]


def test_backfill_project_resolution_errors_exit_two(test_dsn: str) -> None:
    runner = CliRunner()
    result = runner.invoke(root_app, ["render", "backfill", "no-such-project", "--dsn", test_dsn])
    assert result.exit_code == 2


def test_root_cli_mounts_render_group() -> None:
    runner = CliRunner()
    result = runner.invoke(root_app, ["--help"])
    assert result.exit_code == 0
    assert "render" in result.output
