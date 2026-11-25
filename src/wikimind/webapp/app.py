"""FastAPI routes for the local interface prototype.

The app factory accepts an injected service, so TestClient tests never need a
database, API keys, or a network. Importing this module has no configuration
side effects: the real service is built lazily on the first request.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..config import redact
from . import pages
from .service import WebService, build_service

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class AddRequest(BaseModel):
    """Either a named local file or uploaded bytes, never both."""

    path: str | None = None
    filename: str | None = None
    content_base64: str | None = None


class AskRequest(BaseModel):
    question: str = Field(default="", max_length=4000)


def _project(service: WebService) -> str:
    config = getattr(service, "config", None)
    return str(getattr(config, "project_ref", "")) if config is not None else ""


def _safe_html(service: WebService, action: Callable[[], str]) -> HTMLResponse:
    try:
        return HTMLResponse(action())
    except Exception as exc:  # noqa: BLE001 - a dev tool must never leak a DSN in a traceback
        return HTMLResponse(pages.error_page(redact(str(exc)), project=_project(service)))


def create_app(*, service: WebService | None = None) -> FastAPI:
    """Build the app. ``service=None`` wires the sandbox on first request."""

    app = FastAPI(title="WikiMind local interface", docs_url=None, redoc_url=None)
    app.state.service = service

    def svc() -> WebService:
        if app.state.service is None:
            app.state.service = build_service()
        return app.state.service

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        return _safe_html(
            svc(),
            lambda: pages.index_page(_project(svc()), len(svc().list_documents())),
        )

    @app.get("/documents", response_class=HTMLResponse)
    def documents() -> HTMLResponse:
        service = svc()
        return _safe_html(
            service,
            lambda: pages.documents_page(service.list_documents(), _project(service)),
        )

    @app.get("/add", response_class=HTMLResponse)
    def add_form() -> HTMLResponse:
        service = svc()
        config = getattr(service, "config", None)
        files = service.selectable_files()
        message = (
            ""
            if config is None or getattr(config, "live", False)
            else (
                "Real model calls are disabled. Set WIKIMIND_LIVE=1 before compiling; "
                "the pages remain browsable without it."
            )
        )
        return HTMLResponse(pages.add_page(files, project=_project(service), message=message))

    @app.post("/add")
    def add_start(payload: AddRequest) -> JSONResponse:
        service = svc()
        config = getattr(service, "config", None)
        upload_dir = Path(getattr(config, "upload_dir", "data/webapp/uploads"))
        upload_dir.mkdir(parents=True, exist_ok=True)

        try:
            if payload.path:
                candidate = Path(payload.path).expanduser().resolve()
                base = upload_dir.resolve()
                if not candidate.is_relative_to(base):
                    return JSONResponse(
                        {"error": "selected path is outside the upload directory"}, status_code=400
                    )
                if not candidate.is_file():
                    return JSONResponse({"error": "selected file does not exist"}, status_code=400)
                source = candidate
            elif payload.filename and payload.content_base64 is not None:
                name = Path(payload.filename).name
                if not name or name in {".", ".."}:
                    return JSONResponse({"error": "invalid filename"}, status_code=400)
                raw = base64.b64decode(payload.content_base64, validate=True)
                if not raw:
                    return JSONResponse({"error": "uploaded file is empty"}, status_code=400)
                if len(raw) > _MAX_UPLOAD_BYTES:
                    return JSONResponse({"error": "uploaded file exceeds 10 MiB"}, status_code=400)
                stem, suffix = Path(name).stem, Path(name).suffix
                safe = "".join(char for char in stem if char.isalnum() or char in "-_ ") or "source"
                source = upload_dir / f"{safe.strip()}-{uuid4().hex[:8]}{suffix}"
                source.write_bytes(raw)
                candidate = source.resolve()
            else:
                return JSONResponse(
                    {"error": "provide a local path or an uploaded file"}, status_code=400
                )

            job_id = service.start_ingest(candidate)
            return JSONResponse({"job_id": job_id})
        except (OSError, ValueError, binascii.Error, UnicodeDecodeError) as exc:
            return JSONResponse({"error": redact(str(exc))}, status_code=400)

    @app.get("/add/{job_id}/progress")
    def add_progress(job_id: str) -> JSONResponse:
        progress = svc().progress(job_id)
        if progress is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        return JSONResponse(pages.progress_json(progress))

    @app.get("/add/{job_id}", response_class=HTMLResponse)
    def add_job(job_id: str) -> HTMLResponse:
        service = svc()
        progress = service.progress(job_id)
        if progress is None:
            return HTMLResponse(pages.error_page("unknown job"), status_code=404)
        return HTMLResponse(pages.job_page(progress, project=_project(service)))

    @app.post("/ask", response_class=HTMLResponse)
    def ask_submit(payload: AskRequest) -> HTMLResponse:
        service = svc()
        question = payload.question.strip()
        if not question:
            return HTMLResponse("<div class='notice'>Enter a question to search the wiki.</div>")
        return _safe_html(service, lambda: pages.ask_result_html(service.ask(question)))

    @app.get("/ask", response_class=HTMLResponse)
    def ask_form() -> HTMLResponse:
        return HTMLResponse(pages.ask_page(_project(svc())))

    @app.get("/claims/{claim_id}", response_class=HTMLResponse)
    def claim(claim_id: str) -> HTMLResponse:
        service = svc()
        detail = service.claim_detail(claim_id)
        if detail is None:
            return HTMLResponse(
                pages.error_page("claim not found", project=_project(service)), status_code=404
            )
        return HTMLResponse(pages.claim_page(detail, project=_project(service)))

    return app


app = create_app()
