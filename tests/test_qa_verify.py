"""F1/Ruling 21: machine-verified citations are post-processing, never prompt text."""

from __future__ import annotations

from wikimind.qa.prompts import AnswerCitation, AnswerPayload, PromptClaim
from wikimind.qa.verify import extract_cited_ids, map_cited_id, verify_citations

REAL = PromptClaim(ref="c-aaaa11112222", text="real claim", document_slug="attention-paper")
OTHER = PromptClaim(ref="c-bbbb33334444", text="other claim", document_slug="gan-paper")
AMBIGUOUS = [
    PromptClaim(ref="c-ab1111111111", text="a", document_slug="x"),
    PromptClaim(ref="c-ab2222222222", text="b", document_slug="y"),
]


def _payload(answer: str, citations: list[AnswerCitation]) -> AnswerPayload:
    return AnswerPayload(answer=answer, sufficient=True, citations=citations)


def test_fabricated_citation_removed_real_one_survives() -> None:
    payload = _payload(
        "Real fact [c-aaaa1] and invented fact [c-1234].",
        [
            AnswerCitation(claim_id="c-aaaa1", document_slug="attention-paper"),
            AnswerCitation(claim_id="c-1234", document_slug="made-up-source"),
        ],
    )

    result = verify_citations(payload, [REAL, OTHER])

    assert [citation.claim_id for citation in result.citations] == [REAL.ref]
    assert result.citations[0].document_slug == "attention-paper"
    assert result.unverified_removed == 1


def test_slug_mismatch_is_removed() -> None:
    payload = _payload(
        "A claim [c-aaaa11112222].",
        [AnswerCitation(claim_id=REAL.ref, document_slug="wrong-document")],
    )

    result = verify_citations(payload, [REAL])

    assert result.citations == ()
    assert result.unverified_removed == 1


def test_text_only_citation_is_synthesized_from_the_claim() -> None:
    payload = _payload("A claim [c-aaaa1].", [])

    result = verify_citations(payload, [REAL])

    assert result.citations == (AnswerCitation(claim_id=REAL.ref, document_slug="attention-paper"),)
    assert result.unverified_removed == 0


def test_ambiguous_prefix_is_removed() -> None:
    payload = _payload("Something [c-ab].", [AnswerCitation(claim_id="c-ab", document_slug="x")])

    result = verify_citations(payload, AMBIGUOUS)

    assert result.citations == ()
    assert result.unverified_removed == 1


def test_exact_match_wins_over_prefix_matching() -> None:
    exact = PromptClaim(ref="c-1234", text="exact", document_slug="exact-doc")
    prefixed = PromptClaim(ref="c-1234aaaa", text="prefixed", document_slug="other-doc")

    assert map_cited_id("c-1234", [prefixed, exact]) is exact


def test_extract_cited_ids_dedupes_answer_then_array() -> None:
    payload = _payload(
        "First [c-aaaa1], then [c-bbbb3].",
        [
            AnswerCitation(claim_id="c-aaaa1", document_slug="attention-paper"),
            AnswerCitation(claim_id="c-cccc5", document_slug="third-doc"),
        ],
    )
    assert extract_cited_ids(payload.answer, payload.citations) == [
        "c-aaaa1",
        "c-bbbb3",
        "c-cccc5",
    ]


def test_no_citations_is_a_clean_no_op() -> None:
    result = verify_citations(_payload("No citations here.", []), [REAL])
    assert result.citations == ()
    assert result.unverified_removed == 0
