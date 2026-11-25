"""The Add page's ingest path: parse, register, then run the existing compiler.

No pipeline code is duplicated here. The only additions are a writer lock
around document registration and a reporting wrapper around the two model
clients so stage progress is visible in the browser.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from psycopg.rows import dict_row

from ..compile.graph import register_document, resolve_project
from ..compile.pipeline import compile_project
from ..db.connection import connect
from ..db.locks import with_writer_lock
from ..llm.cost import CostMeter, JsonlCostSink
from ..llm.gemini import GeminiClient
from ..llm.judge import JudgeClient
from ..llm.protocols import LLMClient
from ..llm.secrets import load_dotenv
from ..parse import parse_file
from .config import WebConfig
from .models import IngestOutcome
from .progress import ReportingClient, StageReporter

ClientFactory = Callable[[CostMeter], tuple[LLMClient, LLMClient]]


def live_clients(meter: CostMeter) -> tuple[LLMClient, LLMClient]:
    """The pinned drafter and judge, both using the shared metered ledger."""

    return (
        GeminiClient(meter=meter, temperature=0.0),
        JudgeClient(meter=meter, temperature=0.0),
    )


def compile_document(
    config: WebConfig,
    path: Path,
    reporter: StageReporter,
    *,
    meter: CostMeter | None = None,
    clients: ClientFactory = live_clients,
) -> IngestOutcome:
    """Compile one local source file into the configured sandbox project."""

    config.require_live()
    load_dotenv()
    reporter.note("ingest: opened job")

    conn = connect(config.db_dsn, row_factory=dict_row, autocommit=True)
    try:
        project = resolve_project(conn, config.project_ref)
        parsed = parse_file(path)
        reporter.note(f"parse: {parsed.parser}, {len(parsed.channels)} channel(s)")

        with with_writer_lock(project.id, conn):
            _, created = register_document(conn, project.id, parsed, source_uri=str(path.resolve()))
        reporter.note("register: document " + ("created" if created else "already present"))

        active_meter = meter if meter is not None else CostMeter(JsonlCostSink(config.costs_path))
        drafter, judge = clients(active_meter)
        result = compile_project(
            conn,
            project=project,
            drafter=ReportingClient(drafter, reporter, "compile"),
            judge=ReportingClient(judge, reporter, "judge"),
            meter=active_meter,
        )
        reporter.note("commit: stage complete")
        stats = result.stats
        summary = (
            f"run {result.run_id}: {stats.claims_committed} claim(s) committed, "
            f"{stats.units_seen} unit(s) seen, {stats.units_skipped} skipped"
        )
        return IngestOutcome(
            shadow_cost_usd=result.run_cost_usd,
            claims_committed=stats.claims_committed,
            documents_registered=int(created),
            summary=summary,
        )
    finally:
        conn.close()
