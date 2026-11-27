# Prompt changelog — Lane 20 (citation faithfulness repair, M3b)

Authority: LANE 20 work card; `src/wikimind/compile/prompts.py` is unfrozen for
this lane **only** by Ruling 32B. This log is the record required by that
ruling. No governance document is edited.

## Change 1 — 2026-11-27 — quote-scoped claim wording

**Files:** `src/wikimind/compile/prompts.py` (`ANALYZE_PROMPT`, `WRITE_PROMPT`).

**Diagnosis (from the 7 sealed Lane 14 M3b failures, read once under Ruling
32C):** 6 of 7 non-entailments are the same mode — the claim imports a
specific detail (dataset `Natural Questions`, metric `Exact Match`, model
`Transformer (big)`, task `English-to-German`, `Wikipedia`, `RAG-Sequence`,
"additional forward passes") that lives elsewhere in the source passage but
**not in the short `support_quote`** the external judge is shown. The 7th
interprets ambiguous math notation. Root cause: rule 4 demanded "standalone"
claims, which the drafter satisfied by enriching the claim with document
context, while the stored anchor stayed a ~30-word verbatim substring. The
internal JUDGE sees the full passage, so these claims passed the compile gate;
the external M3b judge sees only the anchor and rejects them.

**Before → after (rules changed; all other prompt text byte-identical):**

- `ANALYZE_PROMPT` rule 3: "rest on a verbatim substring" → an explicit
  quote-only rule: every named entity, number, dataset, metric, model, task,
  date and qualifier in the claim must appear in the `support_quote`; include
  the detail in the quote verbatim or leave it out of the claim.
- `ANALYZE_PROMPT` rule 4: "no pronouns without referents" → expand referents
  using only the exact entity string inside the quote; never import a more
  specific name from elsewhere in the passage.
- `ANALYZE_PROMPT` rule 7: restated as a quote-only check whose verdict
  overrides the wish to be more specific; formulas/notation are quoted, not
  reinterpreted.
- `WRITE_PROMPT` rule 2: "adds nothing beyond it" → the candidate's
  `support_quote` is an explicit ceiling on specificity; drop details the
  quote lacks rather than restating them.

**Rationale:** this tightens the invariant that was already stated in rule 7
("a skeptical reader must be able to check each claim against its
support_quote alone") and makes the DISTILL output match what M3b measures.
No schema, cost, model pin, or cross-lane file is touched.

**Digest guards:** `tests/test_compile_prompts.py` golden digests updated for
the two changed prompts only (the test's own docstring prescribes this when a
governed prompt change lands): ANALYZE
`f3fdb519…9416a8`, WRITE `d6454aff…a558`, JUDGE unchanged
`2b56cc42…40589`. `COMPILE_PROMPT_VERSION` deliberately stays `compile-v0`:
forcing regeneration of the sealed corpus is not authorized by this lane.

**Verification status:** NOT VERIFIED by re-measurement — the Lane 20 judging
run hit a real OpenRouter HTTP 429 (free-tier daily limit exhausted) and the
Ruling 7 guard halted all further judge calls. See `LANE20-REPORT.md` and
`HALT-429.json`. Static verification (ruff, pytest) is green. A re-measurement
on fresh fixture pairs is REQUIRED before this change is merged.
