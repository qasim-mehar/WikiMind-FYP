from __future__ import annotations

import pytest

from wikimind.llm.json_utils import parse_json_object


def test_parses_plain_object() -> None:
    assert parse_json_object('{"ok": true}') == {"ok": True}


def test_parses_fenced_object() -> None:
    assert parse_json_object('```json\n{"ok": true}\n```') == {"ok": True}


def test_skips_prose_braces_before_object() -> None:
    text = 'The answer uses {braces} in prose, then: {"ok": true}'
    assert parse_json_object(text) == {"ok": True}


def test_skips_prose_after_object() -> None:
    text = '{"ok": true} trailing commentary'
    assert parse_json_object(text) == {"ok": True}


def test_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        parse_json_object("[1, 2, 3]")


def test_rejects_no_object() -> None:
    with pytest.raises(ValueError):
        parse_json_object("no json here")
