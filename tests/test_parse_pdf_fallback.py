from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from wikimind.parse import (
    ChannelBuilder,
    ParsedDocument,
    ParseError,
    parse_bytes,
    parse_file,
)
from wikimind.parse import pdf_fallback as pdf
from wikimind.parse.ids import channel_id, normalize_text

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "papers"

PDF_CASES = [
    (
        "1706.03762-attention.pdf",
        "Attention Is All You Need",
    ),
    (
        "1406.2661-gan.pdf",
        "Generative Adversarial Nets",
    ),
    (
        "2005.11401-retro.pdf",
        "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
    ),
]


@pytest.mark.parametrize("filename,expected_title", PDF_CASES)
def test_pdf_fallback_parses_fixture(filename: str, expected_title: str) -> None:
    path = FIXTURES / filename
    raw = path.read_bytes()
    document = parse_file(path)

    assert document.parser == "pdf-fallback"
    assert document.source_filename == filename
    assert document.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert document.source_bytes == len(raw)
    assert document.page_count is not None and document.page_count > 0

    titles = document.channels_of_kind("title")
    assert len(titles) == 1
    assert titles[0].text == expected_title
    assert titles[0].ordinal == 0

    abstracts = document.channels_of_kind("abstract")
    assert len(abstracts) == 1
    assert len(abstracts[0].normalized) > 100
    assert not abstracts[0].text.lower().startswith("abstract")

    sections = document.channels_of_kind("section")
    assert len(sections) >= 5
    assert sections[0].text.split("\n", 1)[0] == "1 Introduction"

    references = document.channels_of_kind("reference")
    assert len(references) >= 20
    assert references[0].text.startswith("[1]")
    assert all(reference.text.startswith("[") for reference in references)

    assert document.channels_of_kind("figure_caption")
    assert document.channels_of_kind("table")

    for channel in document.channels:
        assert channel.text.strip()
        assert channel.normalized == normalize_text(channel.text)
        assert channel.channel_id == channel_id(channel.kind.value, channel.normalized)
        assert channel.page is not None
        assert 1 <= channel.page <= document.page_count


@pytest.mark.parametrize("filename,expected_title", PDF_CASES)
def test_pdf_fallback_json_round_trip(filename: str, expected_title: str) -> None:
    document = parse_file(FIXTURES / filename)
    payload = document.model_dump_json()
    restored = ParsedDocument.model_validate_json(payload)
    assert restored == document
    assert restored.model_dump_json() == payload
    assert restored.channels_of_kind("title")[0].text == expected_title


def test_pdf_fallback_rejects_non_pdf_bytes() -> None:
    with pytest.raises(ParseError):
        parse_bytes(b"this is not a pdf", "bad.pdf")


def test_text_parser_rejects_pdf_bytes() -> None:
    raw = (FIXTURES / "1706.03762-attention.pdf").read_bytes()
    with pytest.raises(ParseError):
        parse_bytes(raw, "1706.03762-attention.pdf", parser="text")


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_encrypted_pdf_raises_parse_error() -> None:
    with pytest.raises(ParseError, match="encrypted PDF"):
        parse_bytes(_encrypted_pdf(), "locked.pdf")


def test_empty_password_encrypted_pdf_is_readable() -> None:
    reader = PdfReader(FIXTURES / "1406.2661-gan.pdf")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("")
    buffer = io.BytesIO()
    writer.write(buffer)

    document = parse_bytes(buffer.getvalue(), "gan-locked.pdf")
    assert document.channels_of_kind("title")[0].text == "Generative Adversarial Nets"


def test_blank_pdf_raises_parse_error() -> None:
    with pytest.raises(ParseError, match="no extractable text"):
        parse_bytes(_blank_pdf(), "blank.pdf")


def test_page_access_failure_raises_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenReader:
        is_encrypted = False

        @property
        def pages(self) -> list[object]:
            raise RuntimeError("broken page tree")

    monkeypatch.setattr(pdf, "PdfReader", lambda stream: BrokenReader())
    with pytest.raises(ParseError, match="could not read PDF pages"):
        parse_bytes(b"%PDF-1.4 fake", "broken.pdf")


def test_abstract_keeps_blank_separated_paragraphs() -> None:
    lines = [
        pdf._Line(1, "Abstract"),
        pdf._Line(1, "First paragraph."),
        pdf._Line(1, ""),
        pdf._Line(1, "Second paragraph."),
        pdf._Line(1, "1 Introduction"),
    ]
    abstract, start, end = pdf._find_abstract(lines, 0)
    assert abstract == "First paragraph.\nSecond paragraph."
    assert start == 0
    assert end == 4


def test_abstract_stops_at_page_number() -> None:
    lines = [
        pdf._Line(1, "Abstract"),
        pdf._Line(1, "First paragraph."),
        pdf._Line(1, ""),
        pdf._Line(2, "2"),
        pdf._Line(2, "1 Introduction"),
    ]
    abstract, start, end = pdf._find_abstract(lines, 0)
    assert abstract == "First paragraph."
    assert start == 0
    assert end == 3


def test_reference_entry_markers() -> None:
    builder = ChannelBuilder()
    lines = [
        pdf._Line(1, "[1]"),
        pdf._Line(1, "[2]Author"),
        pdf._Line(1, "[3](ed.)"),
        pdf._Line(1, "4.Title"),
        pdf._Line(1, "[2020] not an entry"),
        pdf._Line(1, "455."),
        pdf._Line(1, "1.2 not an entry"),
        pdf._Line(1, "[5]2020"),
    ]
    pdf._emit_references(lines, builder)
    references = builder.finish()

    assert len(references) == 5
    assert references[0].text == "[1]"
    assert references[1].text == "[2]Author"
    assert references[2].text == "[3](ed.)"
    assert references[3].text.startswith("4.Title")
    assert "[2020]" in references[3].text
    assert "455." in references[3].text
    assert "1.2 not an entry" in references[3].text
    assert references[4].text == "[5]2020"


def test_known_heading_is_case_insensitive() -> None:
    assert pdf._heading_title("introduction") == "introduction"
    assert pdf._heading_title("Introduction") == "Introduction"
    assert pdf._heading_title("experiments.") is None
