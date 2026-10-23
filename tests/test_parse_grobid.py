from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from wikimind.parse import GrobidError, GrobidUnavailableError
from wikimind.parse import grobid as grobid_module
from wikimind.parse.grobid import parse_grobid, parse_tei, probe_grobid

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEI = FIXTURES / "grobid-sample.tei.xml"
PDF_BYTES = b"%PDF-1.4 synthetic"


class FakeResponse:
    def __init__(
        self, status_code: int = 200, content: bytes = b"", text: str = ""
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.text = text


def test_parse_tei_contract() -> None:
    document = parse_tei(
        TEI.read_bytes(), data=PDF_BYTES, source_filename="sample.pdf"
    )

    assert document.parser == "grobid"
    assert document.source_filename == "sample.pdf"
    assert document.source_sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert document.source_bytes == len(PDF_BYTES)
    assert document.page_count is None

    assert [c.kind.value for c in document.channels] == [
        "title",
        "abstract",
        "section",
        "table",
        "section",
        "section",
        "figure_caption",
        "reference",
        "reference",
    ]
    assert document.channels_of_kind("title")[0].text == "A Synthetic TEI Paper"
    abstract = document.channels_of_kind("abstract")[0].text
    assert "synthetic abstract" in abstract
    assert "second paragraph" in abstract
    assert "\n\n" in abstract

    sections = document.channels_of_kind("section")
    assert sections[0].text.startswith("1 Introduction\n\n")
    assert sections[1].text.startswith("2 Method\n\n")
    assert sections[2].text.startswith("2.1 Details\n\n")
    assert "Table 1: Synthetic results" in document.channels_of_kind("table")[0].text
    assert (
        "Figure 1: Overview"
        in document.channels_of_kind("figure_caption")[0].text
    )
    references = document.channels_of_kind("reference")
    assert "First synthetic reference" in references[0].text
    assert "Second synthetic reference" in references[1].text
    assert all(c.page is None for c in document.channels)


def test_parse_tei_rejects_invalid_xml_and_wrong_root() -> None:
    with pytest.raises(GrobidError):
        parse_tei(b"<broken", data=PDF_BYTES, source_filename="x.pdf")
    with pytest.raises(GrobidError):
        parse_tei(b"<root/>", data=PDF_BYTES, source_filename="x.pdf")


def test_parse_grobid_requires_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WIKIMIND_GROBID_URL", raising=False)
    with pytest.raises(GrobidUnavailableError):
        parse_grobid(PDF_BYTES, "x.pdf")


def test_parse_grobid_posts_pdf_and_parses_tei(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WIKIMIND_GROBID_URL", "http://localhost:8070/")
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(200, TEI.read_bytes(), "")

    monkeypatch.setattr(grobid_module.httpx, "post", fake_post)
    document = parse_grobid(PDF_BYTES, "x.pdf")

    assert captured["url"] == "http://localhost:8070/api/processFulltextDocument"
    files = captured["files"]
    assert isinstance(files, dict)
    assert files["input"] == ("x.pdf", PDF_BYTES, "application/pdf")
    assert document.parser == "grobid"
    assert document.source_sha256 == hashlib.sha256(PDF_BYTES).hexdigest()


def test_parse_grobid_accepts_full_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = "http://localhost:8070/api/processFulltextDocument"
    monkeypatch.setenv("WIKIMIND_GROBID_URL", endpoint)
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        captured["url"] = url
        return FakeResponse(200, TEI.read_bytes(), "")

    monkeypatch.setattr(grobid_module.httpx, "post", fake_post)
    parse_grobid(PDF_BYTES, "x.pdf")
    assert captured["url"] == endpoint


def test_parse_grobid_raises_on_http_and_network_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WIKIMIND_GROBID_URL", "http://localhost:8070")

    def http_error(url: str, **kwargs: object) -> FakeResponse:
        return FakeResponse(503, b"", "unavailable")

    monkeypatch.setattr(grobid_module.httpx, "post", http_error)
    with pytest.raises(GrobidError, match="HTTP 503"):
        parse_grobid(PDF_BYTES, "x.pdf")

    def network_error(url: str, **kwargs: object) -> FakeResponse:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(grobid_module.httpx, "post", network_error)
    with pytest.raises(GrobidError, match="request failed"):
        parse_grobid(PDF_BYTES, "x.pdf")


def test_probe_grobid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WIKIMIND_GROBID_URL", raising=False)
    ok, detail = probe_grobid()
    assert not ok
    assert "WIKIMIND_GROBID_URL" in detail

    monkeypatch.setenv("WIKIMIND_GROBID_URL", "http://localhost:8070")
    monkeypatch.setattr(
        grobid_module.httpx,
        "get",
        lambda url, timeout: FakeResponse(200, b"", ""),
    )
    ok, detail = probe_grobid()
    assert ok
    assert "reachable" in detail

    monkeypatch.setattr(
        grobid_module.httpx,
        "get",
        lambda url, timeout: FakeResponse(503, b"", ""),
    )
    ok, _ = probe_grobid()
    assert not ok

    def network_error(url: str, timeout: float) -> FakeResponse:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(grobid_module.httpx, "get", network_error)
    ok, detail = probe_grobid()
    assert not ok
    assert "could not reach" in detail


def test_probe_grobid_accepts_full_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "WIKIMIND_GROBID_URL",
        "http://localhost:8070/api/processFulltextDocument",
    )
    captured: dict[str, str] = {}

    def fake_get(url: str, timeout: float) -> FakeResponse:
        captured["url"] = url
        return FakeResponse(200, b"", "")

    monkeypatch.setattr(grobid_module.httpx, "get", fake_get)
    ok, _ = probe_grobid()
    assert ok
    assert captured["url"] == "http://localhost:8070/api/health"
