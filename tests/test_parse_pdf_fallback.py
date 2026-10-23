from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from wikimind.parse import ParsedDocument, ParseError, parse_bytes, parse_file
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
def test_pdf_fallback_parses_fixture(
    filename: str, expected_title: str
) -> None:
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
        assert channel.channel_id == channel_id(
            channel.kind.value, channel.normalized
        )
        assert channel.page is not None
        assert 1 <= channel.page <= document.page_count


@pytest.mark.parametrize("filename,expected_title", PDF_CASES)
def test_pdf_fallback_json_round_trip(
    filename: str, expected_title: str
) -> None:
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
