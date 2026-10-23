from __future__ import annotations

from wikimind.parse import filter_known, parse_bytes


def test_filter_known_drops_known_documents() -> None:
    first = parse_bytes(b"first", "first.txt")
    second = parse_bytes(b"second", "second.txt")
    third = parse_bytes(b"third", "third.txt")

    result = filter_known([first, second, third], {first.source_sha256})

    assert result == [second, third]
    assert [doc.source_filename for doc in result] == ["second.txt", "third.txt"]


def test_filter_known_preserves_order_and_identity() -> None:
    documents = [parse_bytes(f"doc-{i}".encode(), f"{i}.txt") for i in range(5)]
    result = filter_known(documents, {documents[1].source_sha256})
    assert result == [documents[0], documents[2], documents[3], documents[4]]
    assert result[0] is documents[0]


def test_filter_known_empty_set_returns_all() -> None:
    documents = [parse_bytes(b"a", "a.txt"), parse_bytes(b"b", "b.txt")]
    assert filter_known(documents, set()) == documents


def test_filter_known_is_pure_and_does_not_mutate_inputs() -> None:
    documents = [parse_bytes(b"a", "a.txt"), parse_bytes(b"b", "b.txt")]
    known = {documents[0].source_sha256}
    original_documents = list(documents)
    original_known = set(known)

    first = filter_known(documents, known)
    second = filter_known(documents, known)

    assert first == second
    assert documents == original_documents
    assert known == original_known


def test_filter_known_is_exact_case_sensitive_match() -> None:
    document = parse_bytes(b"a", "a.txt")
    upper = document.source_sha256.upper()
    assert filter_known([document], {upper}) == [document]
