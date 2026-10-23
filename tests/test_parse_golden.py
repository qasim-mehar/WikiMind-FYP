from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from wikimind.parse import parse_file

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "papers"

GOLDEN_CASES = [
    ("contradiction-note.txt", "text"),
    ("1706.03762-attention.pdf", "pdf-fallback"),
    ("1406.2661-gan.pdf", "pdf-fallback"),
    ("2005.11401-retro.pdf", "pdf-fallback"),
]


@pytest.mark.parametrize("filename,parser", GOLDEN_CASES)
def test_channel_ids_are_identical_across_two_parses(
    filename: str, parser: str
) -> None:
    path = FIXTURES / filename
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    first = parse_file(path, parser=parser)
    second = parse_file(path, parser=parser)

    first_ids = [channel.channel_id for channel in first.channels]
    second_ids = [channel.channel_id for channel in second.channels]

    assert first_ids == second_ids
    assert first.model_dump_json() == second.model_dump_json()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_cli_json_is_identical_across_processes() -> None:
    path = FIXTURES / "1706.03762-attention.pdf"
    command = [
        sys.executable,
        "-m",
        "wikimind.parse.cli",
        "run",
        str(path),
        "--json",
    ]

    first = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    second = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert first.stdout == second.stdout
    parsed = json.loads(first.stdout)
    assert parsed["parser"] == "pdf-fallback"
    assert parsed["channels"] == json.loads(second.stdout)["channels"]
