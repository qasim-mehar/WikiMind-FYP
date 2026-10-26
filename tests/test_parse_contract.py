from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wikimind.parse import (
    Channel,
    ChannelKind,
    ParsedDocument,
    UnknownParserError,
    parse_bytes,
    parse_file,
)
from wikimind.parse.ids import channel_id, normalize_text
from wikimind.parse.models import make_document

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOTE = FIXTURES / "papers" / "contradiction-note.txt"

HELLO_WORLD_ID = "6aabb904a8fdd5729f484143c6dd581aa8e12d6fc8d7b6bc1309fc4507da9c94"
CAFE_ID = "55f3396ccc7f1461d7d38a166ad45c547c5f70775c6b9dc03907131a31d10b79"


def test_normalization_is_nfc_and_collapses_whitespace() -> None:
    assert normalize_text("  Hello\u00a0World  ") == "Hello World"
    assert normalize_text("A\tB\nC\r\nD") == "A B C D"
    assert normalize_text("Cafe\u0301") == "Café"


def test_known_channel_id_vectors() -> None:
    assert channel_id("section", "Hello World") == HELLO_WORLD_ID
    assert channel_id("title", "Café") == CAFE_ID


def test_channel_derives_normalized_and_id() -> None:
    channel = Channel(kind="section", ordinal=0, text="  Hello\u00a0World  ")
    assert channel.normalized == "Hello World"
    assert channel.channel_id == HELLO_WORLD_ID
    assert channel.source_ref == f"channel:{HELLO_WORLD_ID}"
    assert channel.page is None


def test_channel_nfc_equivalence_produces_same_id() -> None:
    composed = Channel(kind="title", ordinal=0, text="Café")
    decomposed = Channel(kind="title", ordinal=0, text="Cafe\u0301")
    assert composed.channel_id == decomposed.channel_id == CAFE_ID


def test_channel_rejects_tampered_derived_fields() -> None:
    with pytest.raises(ValidationError):
        Channel(
            kind="section",
            ordinal=0,
            text="Hello",
            normalized="something else",
        )
    with pytest.raises(ValidationError):
        Channel(
            kind="section",
            ordinal=0,
            text="Hello",
            channel_id="0" * 64,
        )


def test_channel_rejects_invalid_kind_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Channel(kind="paragraph", ordinal=0, text="x")
    with pytest.raises(ValidationError):
        Channel(kind="section", ordinal=0, text="x", surprise=True)


def test_channel_rejects_explicit_null_derived_fields() -> None:
    base = {"kind": "section", "ordinal": 0, "text": "x"}
    with pytest.raises(ValidationError):
        Channel.model_validate({**base, "normalized": None})
    with pytest.raises(ValidationError):
        Channel.model_validate({**base, "channel_id": None})


def test_channel_json_round_trip() -> None:
    channel = Channel(kind="figure_caption", ordinal=2, text=" Figure 1: X ", page=4)
    payload = channel.model_dump_json()
    restored = Channel.model_validate_json(payload)
    assert restored == channel
    assert restored.model_dump_json() == payload


def test_kind_enum_is_the_closed_set() -> None:
    assert {kind.value for kind in ChannelKind} == {
        "title",
        "abstract",
        "section",
        "table",
        "figure_caption",
        "reference",
    }


def test_parse_note_document_fields_and_round_trip() -> None:
    document = parse_file(NOTE)
    raw = NOTE.read_bytes()

    assert document.schema_version == 1
    assert document.parser == "text"
    assert document.source_filename == "contradiction-note.txt"
    assert document.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert document.source_bytes == len(raw)
    assert document.page_count is None
    assert len(document.channels) == 1
    assert document.channels[0].kind is ChannelKind.SECTION
    assert document.channels[0].ordinal == 0

    payload = document.model_dump_json()
    restored = ParsedDocument.model_validate_json(payload)
    assert restored == document
    assert restored.model_dump_json() == payload

    decoded = json.loads(payload)
    assert decoded["channels"][0]["kind"] == "section"
    assert decoded["channels"][0]["normalized"]
    assert len(decoded["channels"][0]["channel_id"]) == 64


def test_parsed_document_rejects_non_contiguous_ordinals() -> None:
    first = Channel(kind="section", ordinal=0, text="one")
    second = Channel(kind="section", ordinal=2, text="two")
    with pytest.raises(ValidationError):
        ParsedDocument(
            parser="text",
            source_filename="x.txt",
            source_sha256="0" * 64,
            source_bytes=1,
            channels=(first, second),
        )


def test_channels_of_kind_preserves_order() -> None:
    document = parse_bytes(b"one", "a.txt")
    assert document.channels_of_kind("section") == document.channels
    assert document.channels_of_kind(ChannelKind.TITLE) == ()


def test_make_document_uses_basename_and_raw_hash() -> None:
    document = make_document("text", r"some\dir\name.txt", b"abc", [])
    assert document.source_filename == "name.txt"
    assert document.source_sha256 == hashlib.sha256(b"abc").hexdigest()
    assert document.source_bytes == 3


def _document_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "parser": "text",
        "source_filename": "x.txt",
        "source_sha256": "0" * 64,
        "source_bytes": 1,
        "channels": (),
    }
    base.update(overrides)
    return base


def test_document_rejects_unknown_parser_and_schema_version() -> None:
    with pytest.raises(ValidationError):
        ParsedDocument(**_document_kwargs(parser="nonsense"))
    with pytest.raises(ValidationError):
        ParsedDocument(**_document_kwargs(schema_version=2))


def test_document_accepts_a_registered_custom_parser(monkeypatch) -> None:
    from wikimind.parse import registry

    monkeypatch.setitem(registry._PARSERS, "custom", lambda data, source_filename: None)
    document = ParsedDocument(**_document_kwargs(parser="custom"))
    assert document.parser == "custom"


def test_document_rejects_path_bearing_filename() -> None:
    with pytest.raises(ValidationError):
        ParsedDocument(**_document_kwargs(source_filename="dir/x.txt"))


def test_document_rejects_parser_page_mismatches() -> None:
    section = Channel(kind="section", ordinal=0, text="x")
    with pytest.raises(ValidationError):
        ParsedDocument(**_document_kwargs(page_count=3))

    with pytest.raises(ValidationError):
        ParsedDocument(
            **_document_kwargs(
                parser="pdf-fallback",
                page_count=None,
                channels=(section,),
            )
        )

    with pytest.raises(ValidationError):
        ParsedDocument(
            **_document_kwargs(
                parser="pdf-fallback",
                page_count=1,
                channels=(section,),
            )
        )

    with pytest.raises(ValidationError):
        ParsedDocument(
            **_document_kwargs(
                parser="pdf-fallback",
                page_count=1,
                channels=(Channel(kind="section", ordinal=0, text="x", page=2),),
            )
        )

    with pytest.raises(ValidationError):
        ParsedDocument(
            **_document_kwargs(
                channels=(Channel(kind="section", ordinal=0, text="x", page=1),),
            )
        )


def test_explicit_empty_parser_is_not_a_default() -> None:
    with pytest.raises(UnknownParserError):
        parse_bytes(b"x", "x.txt", parser="")
