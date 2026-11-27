"""Services behind the pages: injected in tests, wired to Postgres in production."""

from __future__ import annotations

import importlib.util
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from psycopg.rows import dict_row

from ..compile.graph import resolve_project
from ..config import redact
from ..db.connection import connect
from ..llm.cost import CostMeter, JsonlCostSink
from ..llm.embedder import FakeEmbedder, SentenceTransformerEmbedder
from ..llm.gemini import GeminiClient
from ..llm.secrets import load_dotenv
from ..qa.cli import QA_BASE_DELAY, QA_TIMEOUT_S
from ..qa.pipeline import answer_question
from ..qa.prompts import CALL_SITE_EMBED
from .config import WebConfig
from .ingest import compile_document
from .models import (
    AskView,
    CitationView,
    ClaimDetail,
    DocumentSummary,
    IngestOutcome,
    JobProgress,
    SelectableFile,
)
from .progress import StageReporter
from .repository import Repository, SqlRepository

Asker = Callable[[str], AskView]
Ingestor = Callable[[Path, StageReporter], IngestOutcome]


class WebService(Protocol):
    """Everything the routes need, with no FastAPI or SQL in the pages."""

    def list_documents(self) -> list[DocumentSummary]: ...

    def claim_detail(self, claim_id: str) -> ClaimDetail | None: ...

    def selectable_files(self) -> list[SelectableFile]: ...

    def ask(self, question: str) -> AskView: ...

    def start_ingest(self, path: Path) -> str: ...

    def progress(self, job_id: str) -> JobProgress | None: ...


def build_embedder(config: WebConfig, meter: CostMeter) -> tuple[object, str]:
    """Return the query embedder and a short, honest label for the page."""

    if config.embedder == "fake" or importlib.util.find_spec("sentence_transformers") is None:
        return (
            FakeEmbedder(meter=meter, call_site=CALL_SITE_EMBED),
            "deterministic local embedder (keyless fallback)",
        )
    return (
        SentenceTransformerEmbedder(meter=meter, call_site=CALL_SITE_EMBED),
        "BAAI/bge-small-en-v1.5 local embedder",
    )


class SqlWebService:
    """Real service: project-scoped SQL, metered clients, background ingest."""

    def __init__(
        self,
        config: WebConfig,
        *,
        repo: Repository | None = None,
        asker: Asker | None = None,
        ingestor: Ingestor | None = None,
        run_inline: bool = False,
    ) -> None:
        self.config = config
        self.repo: Repository = repo if repo is not None else SqlRepository(config)
        self._asker: Asker = asker if asker is not None else self._ask_live
        self._ingestor: Ingestor = (
            ingestor
            if ingestor is not None
            else lambda path, rep: compile_document(config, path, rep)
        )
        self.run_inline = run_inline
        self._jobs: dict[str, StageReporter] = {}
        self._jobs_lock = threading.Lock()

    def list_documents(self) -> list[DocumentSummary]:
        return self.repo.list_documents()

    def claim_detail(self, claim_id: str) -> ClaimDetail | None:
        return self.repo.claim_detail(claim_id)

    def selectable_files(self) -> list[SelectableFile]:
        return self.repo.selectable_files()

    def ask(self, question: str) -> AskView:
        return self._asker(question)

    def start_ingest(self, path: Path) -> str:
        job_id = uuid4().hex[:12]
        reporter = StageReporter(job_id)
        with self._jobs_lock:
            self._jobs[job_id] = reporter
        if self.run_inline:
            self._run_job(path, reporter)
        else:
            threading.Thread(
                target=self._run_job, args=(path, reporter), daemon=True, name=f"webapp-{job_id}"
            ).start()
        return job_id

    def progress(self, job_id: str) -> JobProgress | None:
        with self._jobs_lock:
            reporter = self._jobs.get(job_id)
        return None if reporter is None else reporter.snapshot()

    def _run_job(self, path: Path, reporter: StageReporter) -> None:
        try:
            outcome = self._ingestor(path, reporter)
        except Exception as exc:  # noqa: BLE001 - the job page must show a redacted failure
            reporter.fail(redact(str(exc)))
            return
        reporter.finish(
            shadow_cost_usd=outcome.shadow_cost_usd,
            claims_committed=outcome.claims_committed,
            message=outcome.summary,
        )

    def _ask_live(self, question: str) -> AskView:
        self.config.require_live()
        load_dotenv()
        meter = CostMeter(JsonlCostSink(self.config.costs_path))
        embedder, retrieval_note = build_embedder(self.config, meter)
        drafter = GeminiClient(
            meter=meter,
            temperature=0.0,
            base_delay=QA_BASE_DELAY,
            timeout=QA_TIMEOUT_S,
        )
        conn = connect(self.config.db_dsn, row_factory=dict_row, autocommit=True)
        try:
            project = resolve_project(conn, self.config.project_ref)
            result = answer_question(
                conn,
                project.id,
                question,
                embedder=embedder,
                drafter=drafter,
                meter=meter,
            )
        finally:
            conn.close()

        citations: list[CitationView] = []
        for citation in result.citations:
            detail = self.repo.claim_detail(citation.claim_id)
            claim = next(
                (
                    item
                    for item in result.claims
                    if str(item.claim_id) == citation.claim_id or item.ref == citation.claim_id
                ),
                None,
            )
            citations.append(
                CitationView(
                    claim_id=citation.claim_id,
                    ref=detail.ref if detail else (claim.ref if claim else citation.claim_id),
                    document_slug=citation.document_slug,
                    text=detail.text if detail else (claim.text if claim else ""),
                    status=detail.status if detail else (claim.node_status if claim else "unknown"),
                    source_uri=detail.source_uri if detail else citation.document_slug,
                )
            )
        return AskView(
            question=question,
            answer=result.answer,
            sufficient=result.sufficient,
            citations=tuple(citations),
            shadow_cost_usd=result.shadow_cost_usd,
            retrieved_claims=len(result.claims),
            unverified_removed=result.unverified_removed,
            truncated=result.truncated,
            retrieval_note=retrieval_note,
        )


def build_service() -> SqlWebService:
    """Production wiring. Called lazily on the first request, never at import."""

    load_dotenv()
    return SqlWebService(WebConfig.from_env())
