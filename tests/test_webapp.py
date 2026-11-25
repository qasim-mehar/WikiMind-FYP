"""FastAPI TestClient tests for the local interface prototype.

No database, API key, or network is involved: the service is injected with a
fake repository, asker, and ingestor. Live provider paths are exercised only by
the single metered manual smoke run recorded in the lane report.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wikimind.llm.fakes import FakeLLM
from wikimind.prices import FAKE_LLM_MODEL
from wikimind.webapp.app import create_app
from wikimind.webapp.config import WebConfig, WebConfigError
from wikimind.webapp.models import (
    AskView,
    CitationView,
    ClaimDetail,
    DocumentSummary,
    IngestOutcome,
    SelectableFile,
)
from wikimind.webapp.progress import ReportingClient, StageReporter
from wikimind.webapp.service import SqlWebService

CLAIM_ID = "11111111-2222-3333-4444-555555555555"


class FakeRepo:
    def __init__(self) -> None:
        self.documents = [
            DocumentSummary(
                id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                title="Attention Is All You Need",
                slug="attention-is-all-you-need",
                claim_count=7,
                sha256="0123456789abcdef",
                source_uri="/sandbox/uploads/attention.txt",
                parser="text",
            )
        ]
        self.detail = ClaimDetail(
            claim_id=CLAIM_ID,
            ref="c-111122223333",
            text="The model replaces recurrence with attention.",
            anchor_quote="attention instead of recurrence",
            document_slug="attention-is-all-you-need",
            document_title="Attention Is All You Need",
            source_uri="/sandbox/uploads/attention.txt",
            status="active",
        )

    def list_documents(self) -> list[DocumentSummary]:
        return list(self.documents)

    def claim_detail(self, claim_id: str) -> ClaimDetail | None:
        return self.detail if claim_id == CLAIM_ID else None

    def selectable_files(self) -> list[SelectableFile]:
        return [SelectableFile(path=Path("attention.txt"), name="attention.txt", size=42)]


def fake_ingestor(path: Path, reporter: StageReporter) -> IngestOutcome:
    """Stands in for the compiler while still driving the real progress log."""

    reporter.note(f"parse: text, 1 channel(s) [{path.name}]")
    reporter.note("compile: analyze")
    reporter.note("compile: write")
    reporter.note("judge: judge")
    reporter.note("compile: write")
    return IngestOutcome(
        shadow_cost_usd=0.001234,
        claims_committed=2,
        documents_registered=1,
        summary=f"run done for {path.name}",
    )


def make_view(*, sufficient: bool = True) -> AskView:
    return AskView(
        question="What replaces recurrence?",
        answer=(
            "Attention replaces recurrence [c-111122223333]. "
            "An unknown claim is skipped [c-deadbeefdead]."
        ),
        sufficient=sufficient,
        citations=(
            CitationView(
                claim_id=CLAIM_ID,
                ref="c-111122223333",
                document_slug="attention-is-all-you-need",
                text="The model replaces recurrence with attention.",
                status="active",
                source_uri="/sandbox/uploads/attention.txt",
            ),
        ),
        shadow_cost_usd=0.000123,
        retrieved_claims=4,
        unverified_removed=1,
        truncated=True,
        retrieval_note="deterministic local embedder (keyless fallback)",
    )


@pytest.fixture
def config(tmp_path: Path) -> WebConfig:
    return WebConfig(
        db_dsn="postgresql://wikimind@localhost:5432/wikimind_l17_sandbox",
        project_ref="webapp-sandbox",
        costs_path=tmp_path / "costs.jsonl",
        upload_dir=tmp_path / "uploads",
        live=True,
    )


@pytest.fixture
def client(config: WebConfig) -> TestClient:
    view = make_view()
    service = SqlWebService(
        config,
        repo=FakeRepo(),
        asker=lambda question: replace(view, question=question),
        ingestor=fake_ingestor,
        run_inline=True,
    )
    return TestClient(create_app(service=service))


def test_home_links_to_the_three_pages(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    for href in ("/documents", "/add", "/ask"):
        assert f'href="{href}"' in response.text
    assert "webapp-sandbox" in response.text


def test_documents_page_shows_title_slug_and_claim_count(client: TestClient) -> None:
    response = client.get("/documents")
    assert response.status_code == 200
    assert "Attention Is All You Need" in response.text
    assert "attention-is-all-you-need" in response.text
    assert "<td>7</td>" in response.text
    assert "01234567" in response.text


def test_add_page_offers_upload_and_local_selection(client: TestClient) -> None:
    response = client.get("/add")
    assert response.status_code == 200
    assert 'id="file"' in response.text
    assert "attention.txt" in response.text
    assert "Add &amp; compile" in response.text


def test_add_upload_starts_job_with_visible_stage_progress(client: TestClient) -> None:
    payload = base64.b64encode(b"attention instead of recurrence").decode("ascii")
    response = client.post("/add", json={"filename": "paper.txt", "content_base64": payload})
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    progress = client.get(f"/add/{job_id}/progress")
    assert progress.status_code == 200
    data = progress.json()
    assert data["state"] == "done"
    assert data["stages"][0].startswith("parse: text, 1 channel(s) [paper-")
    assert data["stages"][1:] == [
        "compile: analyze",
        "compile: write (x2)",
        "judge: judge",
    ]
    assert data["shadow_cost_usd"] == pytest.approx(0.001234)
    assert data["claims_committed"] == 2

    page = client.get(f"/add/{job_id}")
    assert page.status_code == 200
    assert "compile: analyze" in page.text
    assert "Shadow cost: $0.001234" in page.text


def test_add_rejects_a_path_outside_the_upload_directory(client: TestClient) -> None:
    response = client.post("/add", json={"path": "../../etc/passwd"})
    assert response.status_code == 400
    assert "outside the upload directory" in response.json()["error"]


def test_add_rejects_empty_upload(client: TestClient) -> None:
    response = client.post("/add", json={"filename": "empty.txt", "content_base64": ""})
    assert response.status_code == 400


def test_ask_renders_clickable_citation_with_claim_and_source(client: TestClient) -> None:
    response = client.post("/ask", json={"question": "What replaces recurrence?"})
    assert response.status_code == 200
    assert f'href="/claims/{CLAIM_ID}"' in response.text
    assert "The model replaces recurrence with attention." in response.text
    assert "attention-is-all-you-need" in response.text
    assert "/sandbox/uploads/attention.txt" in response.text
    assert "Evidence insufficient" not in response.text
    assert "retrieved 4 claim(s) (capped)" in response.text


def test_insufficiency_is_displayed_as_evidence_not_as_an_error(client: TestClient) -> None:
    service = SqlWebService(
        WebConfig(
            db_dsn="postgresql://wikimind@localhost:5432/wikimind_l17_sandbox",
            upload_dir=Path("unused"),
            live=True,
        ),
        repo=FakeRepo(),
        asker=lambda question: make_view(sufficient=False),
        ingestor=fake_ingestor,
        run_inline=True,
    )
    response = TestClient(create_app(service=service)).post("/ask", json={"question": "why?"})
    assert response.status_code == 200
    assert "Evidence insufficient" in response.text
    assert "nothing was invented" in response.text
    assert 'class="error"' not in response.text


def test_ask_empty_question_is_a_prompt_not_an_error(client: TestClient) -> None:
    response = client.post("/ask", json={"question": "   "})
    assert response.status_code == 200
    assert "Enter a question" in response.text


def test_claim_page_shows_claim_anchor_and_source_document(client: TestClient) -> None:
    response = client.get(f"/claims/{CLAIM_ID}")
    assert response.status_code == 200
    assert "attention instead of recurrence" in response.text
    assert "Attention Is All You Need" in response.text
    assert "active" in response.text


def test_unknown_claim_is_404(client: TestClient) -> None:
    response = client.get("/claims/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_config_refuses_the_live_corpus_by_name() -> None:
    for dsn in (
        "postgresql://u:p@localhost:5432/wikimind_live_l3",
        "host=localhost dbname=wikimind_live_l3",
    ):
        with pytest.raises(WebConfigError, match="protected"):
            WebConfig.from_env({"WIKIMIND_WEB_DB": dsn})


def test_config_defaults_to_loopback_and_requires_opt_in_for_public_bind() -> None:
    env = {"WIKIMIND_WEB_DB": "postgresql://u:p@localhost:5432/wikimind_l17_sandbox"}
    config = WebConfig.from_env(env)
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.project_ref == "webapp-sandbox"
    assert config.costs_path == Path("data/webapp/costs.jsonl")

    with pytest.raises(WebConfigError, match="no auth"):
        WebConfig.from_env({**env, "WIKIMIND_WEB_HOST": "0.0.0.0"})


def test_config_requires_live_for_real_model_calls() -> None:
    config = WebConfig.from_env(
        {"WIKIMIND_WEB_DB": "postgresql://u:p@localhost:5432/wikimind_l17_sandbox"}
    )
    with pytest.raises(WebConfigError, match="WIKIMIND_LIVE=1"):
        config.require_live()


def test_reporting_client_reads_stage_from_meta_without_mocking_the_provider() -> None:
    reporter = StageReporter("abc123")
    llm = FakeLLM({"ok": True}, model=FAKE_LLM_MODEL)
    wrapped = ReportingClient(llm, reporter, "compile")

    result = wrapped.complete(
        system="system",
        user="user",
        response_schema=None,
        call_site="compile.analyze",
        meta={"stage": "analyze"},
    )
    assert result.json == {"ok": True}
    assert reporter.snapshot().stages == ("compile: analyze",)
    assert llm.calls[0].meta == {"stage": "analyze"}
