"""Golden guards for the pinned distillation prompts (Lane 3 artifacts).

The three prompts are governance-authored and stored verbatim in
``wikimind.compile.prompts``.  Any rewording, translation, or "improvement"
— including whitespace changes — breaks these digest checks.  Changing a
prompt requires a new governance instruction, which updates both the
constant and its digest here. Lane 20's quote-scoped wording change is
authorized by Ruling 32B and logged in PROMPT-CHANGELOG.md; JUDGE_PROMPT
is unchanged.
"""

from __future__ import annotations

import hashlib

from wikimind.compile.prompts import (
    ANALYZE_PROMPT,
    CALL_SITE_ANALYZE,
    CALL_SITE_JUDGE,
    CALL_SITE_WRITE,
    COMPILE_PROMPT_VERSION,
    JUDGE_PROMPT,
    WRITE_PROMPT,
)

GOLDEN_PROMPT_DIGESTS = {
    "ANALYZE_PROMPT": "f3fdb519fc9e75909d24e4eebac77ad6cd5a8d899ffd7329af0ea356da9416a8",
    "WRITE_PROMPT": "d6454affb4a5ef05bc482353db2a41874223ba2e1d07b4c839e221a6e3eea558",
    "JUDGE_PROMPT": "2b56cc42b1e895a44ee532d79674ab6eb0eee1393f39b73d82308ae6a1a40589",
}


def test_analyze_prompt_is_verbatim() -> None:
    assert (
        hashlib.sha256(ANALYZE_PROMPT.encode("utf-8")).hexdigest()
        == (GOLDEN_PROMPT_DIGESTS["ANALYZE_PROMPT"])
    )
    assert ANALYZE_PROMPT.endswith("Passage follows.")


def test_write_prompt_is_verbatim() -> None:
    assert (
        hashlib.sha256(WRITE_PROMPT.encode("utf-8")).hexdigest()
        == (GOLDEN_PROMPT_DIGESTS["WRITE_PROMPT"])
    )
    assert WRITE_PROMPT.endswith("Candidate claim and its neighborhood follow.")


def test_judge_prompt_is_verbatim() -> None:
    assert (
        hashlib.sha256(JUDGE_PROMPT.encode("utf-8")).hexdigest()
        == (GOLDEN_PROMPT_DIGESTS["JUDGE_PROMPT"])
    )
    assert JUDGE_PROMPT.endswith("Passage and claim follow.")


def test_call_sites_use_canonical_prefixes() -> None:
    # L3 addendum C11: the compile.* prefix is part of the canonical
    # call-site taxonomy; the three stages stay separately aggregable.
    assert CALL_SITE_ANALYZE == "compile.analyze"
    assert CALL_SITE_WRITE == "compile.write"
    assert CALL_SITE_JUDGE == "compile.judge"
    for call_site in (CALL_SITE_ANALYZE, CALL_SITE_WRITE, CALL_SITE_JUDGE):
        from wikimind.llm.cost import validate_call_site

        assert validate_call_site(call_site) == call_site


def test_compile_version_and_gate_defaults() -> None:
    assert COMPILE_PROMPT_VERSION == "compile-v0"
    from wikimind.compile import COMPILE_JUDGE_MIN_SCORE

    assert COMPILE_JUDGE_MIN_SCORE == 4
