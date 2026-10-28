"""Compile-pipeline tests: scripted keyless fakes for all five stages, real
Postgres via the conftest template protocol.

Provider calls are fakes; everything else (registration, short-circuits,
dedup, disposition sanity, judge gate, writer-lock commit, provenance) is
exercised against a migrated throwaway database.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from wikimind.compile import (
    COMPILE_PROMPT_VERSION,
    compile_project,
    register_document,
    resolve_project,
)
from wikimind.compile.graph import create_project
from wikimind.compile.pipeline import CompileStats
from wikimind.llm import InMemorySink, LLMResult, Usage
from wikimind.parse.models import ChannelBuilder, make_document


def _connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


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

    def calls_for(self, call_site: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["call_site"] == call_site]


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


def _create_project(conn: psycopg.Connection) -> tuple[UUID, str]:
    project = create_project(conn, f"lane3-{uuid4().hex[:12]}")
    return project.id, project.name


def _claim_rows(conn: psycopg.Connection, project_id: UUID) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT * FROM claims WHERE project_id = %s ORDER BY id", (project_id,)
    ).fetchall()


def _one(
    text: str,
    quote: str,
    entities: list[dict[str, str]],
    relations: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "claims": [
            {
                "claim_id_hint": "c1",
                "text": text,
                "support_quote": quote,
                "entities": entities,
                "relations": relations or [],
                "confidence": "high",
                "notes": "",
            }
        ]
    }


def _write(
    disposition: str = "new",
    text: str = "Alpha joined Beta Labs in 2017.",
    target: str | None = None,
    entities: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "disposition": disposition,
        "target_claim_id": target,
        "text": text,
        "entities": entities if entities is not None else [{"name": "Alpha", "type": "person"}],
        "relations": [],
        "notes": "",
    }


def _judge(entailed: bool = True, score: int = 5) -> dict[str, Any]:
    return {"entailed": entailed, "score": score, "reason": "quoted passage."}


PASSAGE = (
    "Alpha joined Beta Labs in 2017. Alpha previously worked at Gamma. Alpha leads the Delta team."
)


def _happy_script(
    drafter: ScriptedLLM, judge: ScriptedLLM, *, text: str = "Alpha joined Beta Labs in 2017."
) -> None:
    drafter.queue("compile.analyze", _one(text, text, [{"name": "Alpha", "type": "person"}]))
    drafter.queue(
        "compile.write", _write(text=text, entities=[{"name": "Alpha", "type": "person"}])
    )
    judge.queue("compile.judge", _judge(True, 5))


# --------------------------------------------------------------------------
# five stages end-to-end


def test_happy_path_commits_claim_with_full_provenance(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)

    result = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter, judge=judge
    )

    assert result.status == "done"
    assert result.stats.documents_seen == 1
    assert result.stats.units_seen == 1
    assert result.stats.claims_drafted == 1
    assert result.stats.judge_accepted == 1
    assert result.stats.judge_rejected == 0
    assert result.stats.claims_committed == 1
    assert result.stats.dispositions == {
        "new": 1,
        "supersedes": 0,
        "contradicts": 0,
        "duplicate": 0,
        "reject": 0,
    }

    claims = _claim_rows(conn, project_id)
    assert len(claims) == 1
    claim = claims[0]
    assert claim["text"] == "Alpha joined Beta Labs in 2017."
    assert claim["anchor_quote"] == "Alpha joined Beta Labs in 2017."
    assert claim["anchor_span"]["basis"] == "raw"

    citation = conn.execute(
        "SELECT * FROM nodes WHERE id = %s", (claim["citation_node_id"],)
    ).fetchone()
    assert citation["node_type"] == "citation"
    assert citation["slug"].startswith("source:")

    nodes = conn.execute(
        "SELECT slug, node_type FROM nodes WHERE project_id = %s ORDER BY slug",
        (project_id,),
    ).fetchall()
    assert {(n["slug"], n["node_type"]) for n in nodes} == {
        (citation["slug"], "citation"),
        ("alpha", "entity"),
    }

    edges = conn.execute(
        """
        SELECT e.edge_type, src.slug AS src_slug, dst.slug AS dst_slug, e.id AS edge_id
        FROM edges e
        JOIN nodes src ON src.id = e.src_node
        JOIN nodes dst ON dst.id = e.dst_node
        WHERE e.project_id = %s
        """,
        (project_id,),
    ).fetchall()
    assert {(e["src_slug"], e["edge_type"], e["dst_slug"]) for e in edges} == {
        (citation["slug"], "relates_to", "alpha")
    }

    # Claim-level provenance on every edge (db check rule 3).
    orphans = conn.execute(
        """
        SELECT count(*) AS n FROM edges e
        WHERE e.project_id = %s AND NOT EXISTS (
            SELECT 1 FROM edge_sources s WHERE s.edge_id = e.id)
        """,
        (project_id,),
    ).fetchone()["n"]
    assert orphans == 0

    # node_sources covers the citation node and the entity node.
    sources = conn.execute(
        "SELECT count(*) AS n FROM node_sources WHERE project_id = %s", (project_id,)
    ).fetchone()["n"]
    assert sources == 2

    run_row = conn.execute(
        "SELECT * FROM compile_runs WHERE project_id = %s", (project_id,)
    ).fetchone()
    assert run_row["status"] == "done"
    assert run_row["stats"]["units_seen"] == 1
    assert run_row["stats"]["claims_drafted"] == 1
    assert run_row["stats"]["judge_accepted"] == 1

    marker = conn.execute(
        "SELECT * FROM compile_units WHERE project_id = %s", (project_id,)
    ).fetchone()
    assert marker["compile_version"] == COMPILE_PROMPT_VERSION
    conn.close()


def test_idempotent_rerun_makes_zero_llm_calls_and_zero_new_claims(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)
    project = resolve_project(conn, str(project_id))
    first = compile_project(conn, project=project, drafter=drafter, judge=judge)
    assert first.stats.claims_committed == 1

    # Re-run with no scripted responses at all: any LLM call raises.
    empty_drafter, empty_judge = ScriptedLLM(), ScriptedLLM()
    second = compile_project(conn, project=project, drafter=empty_drafter, judge=empty_judge)

    assert second.status == "done"
    assert second.stats.documents_skipped == 1
    assert second.stats.units_seen == 0
    assert second.stats.claims_committed == 0
    assert empty_drafter.calls == [] and empty_judge.calls == []
    assert len(_claim_rows(conn, project_id)) == 1
    conn.close()


def test_changed_document_only_reprocesses_its_units(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    project = resolve_project(conn, str(project_id))
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)
    compile_project(conn, project=project, drafter=drafter, judge=judge)

    # Same filename, different content -> a new document; only it reprocesses.
    changed = PASSAGE + " Beta Labs moved to Ottawa in 2020."
    _register(conn, project_id, "doc1.txt", changed)
    drafter2, judge2 = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter2, judge2, text="Beta Labs moved to Ottawa in 2020.")
    second = compile_project(conn, project=project, drafter=drafter2, judge=judge2)

    assert second.stats.documents_seen == 2
    assert second.stats.documents_skipped == 1  # unchanged document short-circuits
    assert second.stats.units_seen == 1  # only the changed document's unit
    assert second.stats.claims_committed == 1
    conn.close()


def test_supersede_chain_extends_across_documents(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    project = resolve_project(conn, str(project_id))

    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)
    compile_project(conn, project=project, drafter=drafter, judge=judge)
    claim1 = _claim_rows(conn, project_id)[0]["id"]

    def _supersede_doc(filename: str, text: str, target: UUID) -> None:
        _register(conn, project_id, filename, text + " Additional context sentence.")
        d, j = ScriptedLLM(), ScriptedLLM()
        d.queue("compile.analyze", _one(text, text, [{"name": "Alpha", "type": "person"}]))
        d.queue(
            "compile.write",
            _write(
                disposition="supersedes",
                text=text,
                target=f"claim:{target}",
                entities=[{"name": "Alpha", "type": "person"}],
            ),
        )
        j.queue("compile.judge", _judge(True, 4))
        result = compile_project(conn, project=project, drafter=d, judge=j)
        assert result.stats.claims_committed == 1, result.stats

    _supersede_doc("doc2.txt", "Alpha joined Beta Labs in early 2017.", claim1)
    claim2 = next(c["id"] for c in _claim_rows(conn, project_id) if c["id"] != claim1)
    _supersede_doc("doc3.txt", "Alpha joined Beta Labs on 2017-01-05.", claim2)

    rows = conn.execute(
        """
        SELECT e.id AS edge_id,
               array_agg(s.claim_id ORDER BY s.claim_id) AS claims
        FROM edges e
        JOIN edge_sources s ON s.edge_id = e.id
        WHERE e.project_id = %s AND e.edge_type = 'supersedes'
        GROUP BY e.id
        """,
        (project_id,),
    ).fetchall()
    pairs = {frozenset(row["claims"]) for row in rows}
    assert len(pairs) == 2  # the chain grew: one link per supersede
    assert frozenset({claim1, claim2}) in pairs
    claim3 = {c["id"] for c in _claim_rows(conn, project_id)} - {claim1, claim2}
    assert frozenset({claim2, next(iter(claim3))}) in pairs
    conn.close()


def test_contradictions_are_recorded_never_resolved(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    project = resolve_project(conn, str(project_id))
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)
    compile_project(conn, project=project, drafter=drafter, judge=judge)
    claim1 = _claim_rows(conn, project_id)[0]["id"]

    # Cross-document contradiction: doc2 says Alpha joined in 2018.
    _register(conn, project_id, "doc2.txt", "Alpha joined Beta Labs in 2018. Extra context.")
    d2, j2 = ScriptedLLM(), ScriptedLLM()
    d2.queue(
        "compile.analyze",
        _one(
            "Alpha joined Beta Labs in 2018.",
            "Alpha joined Beta Labs in 2018.",
            [{"name": "Alpha", "type": "person"}],
        ),
    )
    d2.queue(
        "compile.write",
        _write(
            disposition="contradicts",
            text="Alpha joined Beta Labs in 2018.",
            target=f"claim:{claim1}",
            entities=[{"name": "Alpha", "type": "person"}],
        ),
    )
    j2.queue("compile.judge", _judge(True, 5))
    result = compile_project(conn, project=project, drafter=d2, judge=j2)

    assert result.stats.dispositions["contradicts"] == 1
    assert result.stats.claims_committed == 1
    # Both claims persist; a contradicts edge links them; no winner picked.
    claims = _claim_rows(conn, project_id)
    assert {c["text"] for c in claims} == {
        "Alpha joined Beta Labs in 2017.",
        "Alpha joined Beta Labs in 2018.",
    }
    link = conn.execute(
        """
        SELECT DISTINCT s.claim_id
        FROM edges e JOIN edge_sources s ON s.edge_id = e.id
        WHERE e.project_id = %s AND e.edge_type = 'contradicts'
        """,
        (project_id,),
    ).fetchall()
    assert {row["claim_id"] for row in link} == {c["id"] for c in claims}
    conn.close()


def test_cross_document_neighborhood_reaches_write(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    project = resolve_project(conn, str(project_id))
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)
    compile_project(conn, project=project, drafter=drafter, judge=judge)
    claim1 = _claim_rows(conn, project_id)[0]["id"]

    _register(conn, project_id, "doc2.txt", "Alpha later left Beta Labs. More text.")
    d2, j2 = ScriptedLLM(), ScriptedLLM()
    d2.queue(
        "compile.analyze",
        _one(
            "Alpha later left Beta Labs.",
            "Alpha later left Beta Labs.",
            [{"name": "Alpha", "type": "person"}],
        ),
    )
    d2.queue(
        "compile.write",
        _write(text="Alpha later left Beta Labs.", entities=[{"name": "Alpha", "type": "person"}]),
    )
    j2.queue("compile.judge", _judge(True, 4))
    compile_project(conn, project=project, drafter=d2, judge=j2)

    write_user = d2.calls_for("compile.write")[0]["user"]
    assert f"claim:{claim1}" in write_user  # neighborhood spans the project
    conn.close()


# --------------------------------------------------------------------------
# gates and counters


@pytest.mark.parametrize(
    ("entailed", "score", "accepted"),
    [(True, 3, False), (True, 4, True), (True, 5, True), (False, 5, False)],
)
def test_judge_threshold_boundary(
    test_dsn: str, entailed: bool, score: int, accepted: bool
) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)
    judge.responses["compile.judge"].clear()
    judge.queue("compile.judge", _judge(entailed, score))

    result = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter, judge=judge
    )

    assert result.stats.judge_accepted == (1 if accepted else 0)
    assert result.stats.judge_rejected == (0 if accepted else 1)
    assert result.stats.claims_committed == (1 if accepted else 0)
    conn.close()


def test_dedup_prefilter_blocks_exact_duplicates_before_any_llm(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    project = resolve_project(conn, str(project_id))
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)
    compile_project(conn, project=project, drafter=drafter, judge=judge)

    # A new document whose analyzer emits the SAME claim text (whitespace variant).
    _register(conn, project_id, "doc2.txt", "Other passage. Nothing new here.")
    d2, j2 = ScriptedLLM(), ScriptedLLM()
    d2.queue(
        "compile.analyze",
        _one(
            "Alpha  joined Beta Labs in 2017.",
            "Other passage.",
            [{"name": "Alpha", "type": "person"}],
        ),
    )
    result = compile_project(conn, project=project, drafter=d2, judge=j2)

    assert result.stats.dedup_filtered == 1
    assert d2.calls_for("compile.write") == [] and j2.calls == []  # never reached an LLM
    assert len(_claim_rows(conn, project_id)) == 1
    conn.close()


def test_anchor_reject_drops_unverifiable_quote(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    drafter.queue(
        "compile.analyze",
        _one(
            "Alpha joined Beta Labs.",
            "not a substring of the passage",
            [{"name": "Alpha", "type": "person"}],
        ),
    )
    result = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter, judge=judge
    )

    assert result.stats.anchor_rejects == 1
    assert drafter.calls_for("compile.write") == []
    assert judge.calls == []
    conn.close()


def test_analyze_schema_reject_marks_unit_and_counts(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    project = resolve_project(conn, str(project_id))
    _register(conn, project_id, "doc1.txt", PASSAGE)
    bad = {"claims": [{"nope": True}]}
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    drafter.queue("compile.analyze", bad, bad)

    result = compile_project(conn, project=project, drafter=drafter, judge=judge)
    assert result.stats.schema_rejects == 1

    # The unit is marked processed: a re-run must not retry it.
    empty_drafter, empty_judge = ScriptedLLM(), ScriptedLLM()
    second = compile_project(conn, project=project, drafter=empty_drafter, judge=empty_judge)
    assert second.stats.documents_skipped == 1
    assert second.stats.units_seen == 0
    conn.close()


def test_write_disposition_repairs_downgrade_to_new(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    project = resolve_project(conn, str(project_id))
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()

    # One analyze response carrying three candidates; each write names a
    # broken target (null, unparseable, nonexistent claim) -> all repaired.
    drafter.queue(
        "compile.analyze",
        {
            "claims": [
                {
                    "claim_id_hint": "c1",
                    "text": "Alpha joined Beta Labs in 2017.",
                    "support_quote": "Alpha joined Beta Labs in 2017.",
                    "entities": [{"name": "Alpha", "type": "person"}],
                    "relations": [],
                    "confidence": "high",
                    "notes": "",
                },
                {
                    "claim_id_hint": "c2",
                    "text": "Alpha previously worked at Gamma.",
                    "support_quote": "Alpha previously worked at Gamma.",
                    "entities": [{"name": "Gamma", "type": "org"}],
                    "relations": [],
                    "confidence": "high",
                    "notes": "",
                },
                {
                    "claim_id_hint": "c3",
                    "text": "Alpha leads the Delta team.",
                    "support_quote": "Alpha leads the Delta team.",
                    "entities": [{"name": "Alpha", "type": "person"}],
                    "relations": [],
                    "confidence": "high",
                    "notes": "",
                },
            ]
        },
    )
    drafter.queue("compile.write", _write(disposition="supersedes", target=None))
    drafter.queue("compile.write", _write(disposition="supersedes", target="not-a-uuid"))
    drafter.queue(
        "compile.write",
        _write(disposition="supersedes", target=f"claim:{UUID(int=999)}"),
    )
    judge.queue("compile.judge", _judge(True, 5), _judge(True, 5), _judge(True, 5))

    result = compile_project(conn, project=project, drafter=drafter, judge=judge)

    assert result.stats.disposition_repairs == 3
    assert result.stats.dispositions["new"] == 3
    assert result.stats.dispositions["supersedes"] == 0
    assert result.stats.claims_committed == 3
    conn.close()


def test_verified_duplicate_commits_nothing(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    project = resolve_project(conn, str(project_id))
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)
    compile_project(conn, project=project, drafter=drafter, judge=judge)
    claim1 = _claim_rows(conn, project_id)[0]["id"]

    # Different wording (not caught by the exact-text pre-filter) that the
    # writer recognises as a duplicate of the existing claim.
    _register(conn, project_id, "doc2.txt", "Alpha started at Beta Labs in 2017. Per this note.")
    d2, j2 = ScriptedLLM(), ScriptedLLM()
    d2.queue(
        "compile.analyze",
        _one(
            "Alpha started at Beta Labs in 2017.",
            "Alpha started at Beta Labs in 2017.",
            [{"name": "Alpha", "type": "person"}],
        ),
    )
    d2.queue(
        "compile.write",
        _write(
            disposition="duplicate",
            target=f"claim:{claim1}",
            entities=[{"name": "Alpha", "type": "person"}],
        ),
    )
    result = compile_project(conn, project=project, drafter=d2, judge=j2)

    assert result.stats.dispositions["duplicate"] == 1
    assert result.stats.claims_committed == 0
    assert j2.calls == []  # nothing to judge
    assert len(_claim_rows(conn, project_id)) == 1
    conn.close()


def test_writer_reject_disposition_is_counted_not_committed(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    drafter.queue(
        "compile.analyze",
        _one(
            "Alpha joined Beta Labs in 2017.",
            "Alpha joined Beta Labs in 2017.",
            [{"name": "Alpha", "type": "person"}],
        ),
    )
    drafter.queue("compile.write", _write(disposition="reject", text="too vague"))

    result = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter, judge=judge
    )

    assert result.stats.dispositions["reject"] == 1
    assert result.stats.claims_committed == 0
    assert judge.calls == []
    conn.close()


def test_judge_schema_reject_counts_and_drops(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    drafter.queue(
        "compile.analyze",
        _one(
            "Alpha joined Beta Labs in 2017.",
            "Alpha joined Beta Labs in 2017.",
            [{"name": "Alpha", "type": "person"}],
        ),
    )
    drafter.queue("compile.write", _write())
    judge.queue("compile.judge", {"wrong": 1}, {"wrong": 2})

    result = compile_project(
        conn, project=resolve_project(conn, str(project_id)), drafter=drafter, judge=judge
    )

    assert result.stats.schema_rejects == 1
    assert result.stats.claims_committed == 0
    conn.close()


def test_run_cost_reads_the_meter(test_dsn: str) -> None:
    conn = _connect(test_dsn)
    project_id, _ = _create_project(conn)
    _register(conn, project_id, "doc1.txt", PASSAGE)
    drafter, judge = ScriptedLLM(), ScriptedLLM()
    _happy_script(drafter, judge)

    from wikimind.llm import CostMeter
    from wikimind.prices import GEMINI_MODEL

    meter = CostMeter(InMemorySink())
    meter.record(call_site="compile.analyze", model=GEMINI_MODEL, in_tokens=1_000_000, out_tokens=0)

    result = compile_project(
        conn,
        project=resolve_project(conn, str(project_id)),
        drafter=drafter,
        judge=judge,
        meter=meter,
    )
    assert result.run_cost_usd == 0.25
    conn.close()


def test_stats_to_dict_shape() -> None:
    stats = CompileStats()
    stats.dispositions["new"] = 2
    data = stats.to_dict()
    assert data["dispositions"] == {
        "new": 2,
        "supersedes": 0,
        "contradicts": 0,
        "duplicate": 0,
        "reject": 0,
    }
    assert "errors" not in data
    stats.errors.append("boom")
    assert stats.to_dict()["errors"] == ["boom"]
