from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wikimind.parse import ParsedDocument, parse_file
from wikimind.parse.grobid import parse_tei

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "papers"
TEI = Path(__file__).resolve().parent / "fixtures" / "grobid-sample.tei.xml"
TEI_PDF_BYTES = b"%PDF-1.4 synthetic"

GOLDEN_CASES = [
    ("contradiction-note.txt", "text"),
    ("1706.03762-attention.pdf", "pdf-fallback"),
    ("1406.2661-gan.pdf", "pdf-fallback"),
    ("2005.11401-retro.pdf", "pdf-fallback"),
]

EXPECTED_FIXTURES = {
    "contradiction-note.txt": {
        "sha256": "4ef572f4fd21de0d5e1865f0055a4a11b60fdf82ec0ec14ebef3b8ae3bf22756",
        "count": 1,
        "snapshot_sha256": "bf8c7f380a2ce2562bf4c127d4193811a08b1ab30f646d13b6eff6450c8f7e93",
    },
    "1706.03762-attention.pdf": {
        "sha256": "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
        "count": 74,
        "snapshot_sha256": "7042e476c970de9d719d799ca15eaa723aef4ed3de466061acb11c6d4be022bc",
    },
    "1406.2661-gan.pdf": {
        "sha256": "ff5819e3a7b713c3bd3107b7de3d51fe0a347aa5d8444f0efdcf2345ef0a8b63",
        "count": 47,
        "snapshot_sha256": "c0e69027b4ef28c02e82eab154db02a0ed8d7b973735adb7c24934bf7f45e7a6",
    },
    "2005.11401-retro.pdf": {
        "sha256": "23e3249e9a1e75418d82efecab0ea8c4d033b89c93742f63208d47ce01f21233",
        "count": 107,
        "snapshot_sha256": "fddcbf60c12dfd08c9f1daaeacbbd47c3ae42120cbe03e52912d3e1d7ce1002b",
    },
    "grobid-sample.tei.xml": {
        "sha256": "debf003f5f25e9c46a47cb8abfb524cb44e94646b63aab3269df7768be488e7b",
        "source_sha256": "105156e58646c5274f4f7420d8cd21020fc3cca3eeffb8453fbd3b5859675a7c",
        "count": 9,
        "snapshot_sha256": "2bd81426ae8f77b5e2765046085015b75dd68b24a449f52361bbe8a79f8711ce",
    },
}


def _snapshot_digest(document: ParsedDocument) -> str:
    return hashlib.sha256(document.model_dump_json().encode("utf-8")).hexdigest()


def test_fixture_hashes_and_full_snapshots() -> None:
    for filename, expected in EXPECTED_FIXTURES.items():
        if filename.endswith(".tei.xml"):
            continue
        path = FIXTURES / filename
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected["sha256"]
        document = parse_file(path)
        assert len(document.channels) == expected["count"]
        assert _snapshot_digest(document) == expected["snapshot_sha256"]


def test_tei_fixture_hash_and_full_snapshot() -> None:
    expected = EXPECTED_FIXTURES["grobid-sample.tei.xml"]
    raw = TEI.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected["sha256"]

    document = parse_tei(raw, data=TEI_PDF_BYTES, source_filename="sample.pdf")
    assert document.source_sha256 == expected["source_sha256"]
    assert len(document.channels) == expected["count"]
    assert _snapshot_digest(document) == expected["snapshot_sha256"]


@pytest.mark.parametrize("filename,parser", GOLDEN_CASES)
def test_channel_ids_are_identical_across_two_parses(filename: str, parser: str) -> None:
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

    first_env = os.environ.copy()
    first_env["PYTHONHASHSEED"] = "0"
    second_env = os.environ.copy()
    second_env["PYTHONHASHSEED"] = "424242"

    first = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=first_env,
    )
    second = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=second_env,
    )

    assert first.stdout == second.stdout
    parsed = json.loads(first.stdout)
    assert parsed["parser"] == "pdf-fallback"
    assert parsed["channels"] == json.loads(second.stdout)["channels"]
