# LANE 20 — Citation Faithfulness Repair (M3b)

**Branch:** `lane/20-faithfulness` · **Base:** `d362783d0c3cd8c825492866bcf87b7311e5cd63`
(verified unchanged: `main` and `origin/main` both at the base at dispatch) ·
**Date:** 2026-11-27

**Honest outcome:** the dominant failure mode is diagnosed and a focused,
quote-scoped prompt repair is landed with green static checks. The required
**re-measurement is BLOCKED** — the pinned non-Gemini judge returned a real
HTTP 429 (OpenRouter free-tier daily quota exhausted) and the Ruling 7 guard
halted all further judge calls. **No post-repair entailment rate is claimed.**
Do not merge this lane until a compliant fresh sample has been judged.

---

## Phase 0 — baseline

| Check | Result |
| --- | --- |
| Base commit | `d362783` — verified, not moved |
| Branch | `lane/20-faithfulness`, created from base, clean tree |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 201 files already formatted |
| pytest (DB, dev `wikimind_l0`) | **536 passed** |
| pytest (no DB) | **371 passed, 165 skipped** |
| Frozen diffs (base → lane) | empty for `DECISIONS.md`, `PROJECT.md`, `V0_PROTOCOL.md`, `PRE_REGISTRATION.md`, `docs/contracts/`, `migrations/`, `src/wikimind/qa/`, `src/wikimind/llm/cost.py`, `src/wikimind/webapp/` |

Raw output: `lane20-evidence/pytest-baseline-{db,nodb}.txt`.

---

## Diagnosis — the 7 Lane 14 non-entailments

The 7 judgments in `lane14-evidence/m3b_judgments.json` were read once under
Ruling 32C. The sealed exam set was **not** used as a development target; no
prompt was tuned against its questions or answers.

| # | Question / ref | What the claim adds beyond its anchor quote | Mode |
| --- | --- | --- | --- |
| 1 | S029 · `c-baab1ede47dd` | `Exact Match (EM)` + `Natural Questions` (quote: only "the 44.5 that RAG-Sequence achieves") | over-specific wording |
| 2 | M023 · `c-baab1ede47dd` | same claim: dataset + metric absent from quote | over-specific wording |
| 3 | M018 · `c-c6a43b8b8b2f` | `RAG-Sequence`, "additional forward passes", "hypothesis probability"; quote names only "Thorough Decoding" | drift beyond passage |
| 4 | S027 · `c-d2677485567b` | `Wikipedia` added to "the December 2018 dump" | added entity |
| 5 | S008 · `c-76bd067114c9` | expands `softmax(QK T√dk )V` into `QK^T / sqrt(dk)` | notation/paraphrase loss |
| 6 | M032 · `c-c24e9f63c086` | `Transformer (big)` + `English-to-German` added to a training-time/hardware quote | added context |
| 7 | M050 · `c-69ab81c8acb4` | `RAG models` added to "406M trainable parameters from BART-large" | added entity |

**Dominant mode: 6 of 7 (86%) are claim over-specification / imported
context** — the claim asserts a detail that is true somewhere in the document
but absent from the ~30-word `anchor_quote` the M3b judge is shown. The 7th
(notation) is a distinct, minority mode and was **not** targeted separately.

### Root cause in the distill path

* `ANALYZE` sees the whole channel (up to 60k chars). Its rule 4 demanded a
  "standalone" claim, which the drafter satisfied by importing document
  context (dataset, metric, model, task) into the claim text.
* The stored `anchor_quote` remains a short verbatim substring (rule 3).
* The **internal** JUDGE in `compile/pipeline.py` grades the final claim
  against the **full passage** (`passage_block(channel_for_unit(unit), …)`),
  so over-specified claims pass the compile gate and are committed.
* The **external** M3b judge grades the claim against the **anchor quote
  alone**, so the same claims fail there. That asymmetry is the defect.

---

## Change landed (dominant mode only)

`src/wikimind/compile/prompts.py` — authorized by Ruling 32B, logged in
`PROMPT-CHANGELOG.md`:

1. `ANALYZE_PROMPT` rule 3 → quote-only rule: every named entity, number,
   dataset, metric, model, task, date and qualifier in the claim **must appear
   in `support_quote`**; include the detail in the quote verbatim or leave it
   out of the claim.
2. `ANALYZE_PROMPT` rule 4 → expand referents only with the exact entity
   string inside the quote; never import a longer name from elsewhere.
3. `ANALYZE_PROMPT` rule 7 → explicit quote-only check that overrides the
   wish to be more specific; notation is quoted, not reinterpreted.
4. `WRITE_PROMPT` rule 2 → the candidate's `support_quote` is the ceiling on
   specificity; drop details the quote lacks instead of restating them.

No schema, migration, model pin, cost code, QA, webapp, or governance file was
touched. `COMPILE_PROMPT_VERSION` intentionally stays `compile-v0` — forcing
regeneration of the sealed corpus is not authorized by this lane.

`tests/test_compile_prompts.py`: golden digests updated for the two changed
prompts only, per that test's own documented procedure:
ANALYZE `f3fdb519…9416a8`, WRITE `d6454aff…a558`, JUDGE unchanged
`2b56cc42…40589`.

---

## Re-measurement — BLOCKED by Ruling 7 guard

Harness: `lane20-evidence/measure.py` (adapted from
`lane14-evidence/judge_entailment.py`, which was never modified). It parses
the dev fixture papers (`tests/fixtures/papers/`), drafts claims at the
ANALYZE stage with the pinned Flash drafter, pairs each claim with its own
verbatim `support_quote`, and judges a fixed random sample (`seed=42`,
shuffled, blind) with the pinned non-Gemini nemotron judge, the same
`judge.m3b_entailment` call site and the same judge system prompt/schema/escaping.

| Result | Value |
| --- | --- |
| Authoritative pre-repair baseline (Lane 14, sealed, n=40) | **33/40 = 0.825** |
| Lane 20 pilot, original prompt, n=15 (non-compliant, discarded) | 9/15 = 0.600 — draft-stage, not comparable to Lane 14 |
| Lane 20 fresh compliant "before" sample | 51 pairs drafted, **0 judged** — halted at first judge call |
| Lane 20 post-repair entailment rate | **NOT COMPUTABLE** |

**Halt:** the judge returned `HTTP 429 — free-models-per-day`, with
`X-RateLimit-Limit: 50`, `X-RateLimit-Remaining: 0`, reset
`2026-11-28T00:00:00Z` (observed `2026-11-27T08:39:18Z`). Per Ruling 7 the
run stopped immediately: no retry, no model substitution, no paid-tier
escalation. Full incident in `lane20-evidence/HALT-429.json`.

**Interpretation:** 0.825 remains the only valid M3b number for this lane's
baseline. The repair is principled and static checks are green, but its
directional effect is **unmeasured**. The next operator must re-run
`python lane20-evidence/measure.py before` and `… after` after the quota
resets (or with a funded key), confirm ≥40 judgments per arm, and only then
decide whether to merge.

### Known limitations of the harness (disclose on re-run)

* Pairs are taken at **ANALYZE output**, not from committed claims after
  WRITE/JUDGE. This is where the dominant over-specification originates, but
  it is a different population from Lane 14's answer-path citations.
* Fixture passages are capped at 2800 chars; a transient Gemini 504 required
  adding bounded 5xx retries (a real 429 still halts).
* Judge cost per call was ~$0.00035 here, below Lane 14's ~$0.00093.

---

## Spend (shadow, production list prices)

| Ledger | Events | Shadow USD |
| --- | --- | --- |
| `lane20-evidence/draft_costs.jsonl` (`compile.analyze`) | 67 | 0.11753000 |
| `lane20-evidence/judge_costs.jsonl` (`judge.m3b_entailment`) | 16 | 0.01728120 |
| **Total** | **83** | **0.13481120** |

Actual cost: **$0.00**. Judge spend is **$0.0173 against the $0.50 ceiling**.
The draft ledger includes the discarded pilot and the aborted run; both are
retained because the ledger is append-only.

---

## Gate C evidence

Manifest: `lane20-evidence/SHA256SUMS.txt`, generated by the Lane 13 CRLF
procedure (`Path.write_text(..., newline=None)` on Windows) with the Ruling 31
`git ls-files` assertion that every listed path is tracked before writing.

* Manifest SHA-256: `0b5ac76be8ba58c43e663adfb953da35b234a7f95fecd18c113c80aa37a51904`
* 12 files listed; 12 CRLF line endings, 0 bare LF (verified at generation).
* All listed paths git-tracked: asserted by `git ls-files` before writing.
* Frozen diffs: empty (verified above).

---

## STOP

Judge calls halted on the 429 guard. Lane is pushed with a clean tree. No
merge, no main push, no live-corpus access. Merging is a human action.
