from __future__ import annotations

from pathlib import Path

import pytest

from wikimind.parse import ParseError, parse_bytes, parse_file
from wikimind.parse.registry import parser_name_for_path, parser_names

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOTE = FIXTURES / "papers" / "contradiction-note.txt"


def test_text_parser_emits_one_section_channel() -> None:
    document = parse_file(NOTE)
    assert document.parser == "text"
    assert document.page_count is None
    assert len(document.channels) == 1
    channel = document.channels[0]
    assert channel.kind.value == "section"
    assert channel.ordinal == 0
    assert channel.text == NOTE.read_text(encoding="utf-8")
    assert channel.normalized == " ".join(channel.text.split())
    assert channel.page is None


def test_text_parser_handles_markdown_as_plain_text(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_bytes(b"# Heading\n\nSome **markdown** text.\n")
    document = parse_file(path)
    assert document.parser == "text"
    assert document.source_filename == "notes.md"
    assert document.channels[0].text == "# Heading\n\nSome **markdown** text.\n"


def test_text_parser_strips_utf8_bom() -> None:
    document = parse_bytes(b"\xef\xbb\xbfhello", "bom.txt")
    assert document.channels[0].text == "hello"


def test_text_parser_empty_and_whitespace_files_have_no_channels() -> None:
    assert parse_bytes(b"", "empty.txt").channels == ()
    assert parse_bytes(b" \n\t\r\n", "blank.txt").channels == ()


def test_text_parser_rejects_non_utf8() -> None:
    with pytest.raises(ParseError, match="UTF-8"):
        parse_bytes(b"\xff\xfe\x00bad", "binary.txt")


def test_parser_name_for_path() -> None:
    assert parser_name_for_path("a.pdf") == "pdf-fallback"
    assert parser_name_for_path("a.PDF") == "pdf-fallback"
    assert parser_name_for_path("a.txt") == "text"
    assert parser_name_for_path("a.md") == "text"
    with pytest.raises(ParseError):
        parser_name_for_path("a.docx")


def test_unknown_extension_fails_before_reading(tmp_path: Path) -> None:
    missing = tmp_path / "missing.docx"
    with pytest.raises(ParseError):
        parse_file(missing)


def test_unknown_explicit_parser_fails_before_reading(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    with pytest.raises(ParseError):
        parse_file(missing, parser="nope")


def test_registered_names_are_stable() -> None:
    assert parser_names() == ("grobid", "pdf-fallback", "text")
