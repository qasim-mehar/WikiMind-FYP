# LANE 14 — HALT REPORT (two halts, Ruling 7 then W5 budget)

> **SUPERSEDED (2026-11-26).** This document records the state at the W2
> budget-breach halt only. The run later resumed under Ruling 30 and completed
> 150/150 questions. For the final verdict table, threshold table, accuracy
> and manifest, see `LANE14-REPORT.md`. The "no verdict table" statement below
> applies only to the halt moment, not to the final state.

**Status at the time of this report: HALTED. This is NOT a completed V0 evaluation. No verdict table, no
threshold table, no accuracy claim is issued (at that time).**

## Halt 1 — window 1 (real HTTP 429, Ruling 7)

- 2026-11-25 23:54:19 PST, question **C020** attempt 1: `429 RESOURCE_EXHAUSTED`,
  HTTP body `code: 429`, quota metric
  `generativelanguage.googleapis.com/generate_content_free_tier_requests`,
  `limit: 500`, quota id `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
  `retryDelay: 343s`. Genuine Gemini free-tier daily quota exhaustion.
- Runner halted immediately; no further questions attempted in window 1.
- Window-1 result: 107 answers, 12 questions exhausted 6 bounded attempts on
  transient 503 (`M023, M056, M058–M060, C001–C007`), C020 halted, 30 unattempted.

## Halt 2 — window 2 (per-question budget breach, governance W5)

- Resumed 2026-11-26 00:32:15 PST after midnight-Pacific quota reset, with a
  fresh read-only corpus proof (`corpus_proof_w2_before.txt`, identical to B1),
  the same isolated `costs.jsonl` appended, `WIKIMIND_COST_DB_MIRROR` unset.
- Governance scope: 31 unanswered + 12 re-opened with a fresh 6-attempt budget
  = 43 questions; full W1 history archived in `answers/window1/` and
  `run_manifest_w1.json`.
- W2 ran exactly one question, **M023**, which succeeded but at a per-question
  shadow of **$0.10386925** (cap $0.02). Cause: one `qa.answer` call emitted
  **65,520 output tokens** ($0.10074675) plus a 434-token retry; 4 ledger events.
- Runner halted immediately at 2026-11-26 00:37:56 PST with
  `{"halt": "per-question budget breach", "id": "M023"}`.
- **No second 429 occurred** in window 2.

## State at this halt (merged `run_manifest.json`, 120 rows)

| Category | Count | IDs |
| --- | ---: | --- |
| Answers obtained (one pass, production defaults) | 108 | all except below |
| Exhausted 6 attempts in W1; W2 re-opened, unrun at halt | 11 | M056, M058, M059, M060, C001–C007 |
| Halted on 429 in W1 | 1 | C020 |
| Never attempted (either window) | 30 | C021–C030, U001–U020 |

- Window-2 completed: M023 only (total attempts across both windows: 7).
- Spend: answer ledger ~$0.397 shadow (see `wikimind llm costs --path
  lane14-evidence/costs.jsonl`); total cap $5.00 not breached, but the
  **per-question cap was** on M023.
- Corpus proof identical to B1 (read-only session verified). Frozen question set
  SHA256 `bc0fd07c0148bc2452b1bca01aa946e093283967c47b116112b20d97a9648dc4`
  and answer key unchanged. No `src/`, `tests/`, or frozen-file edits.

## Next step

Await governance direction: a third window (with an explicit decision on how to
treat runaway-generation cost against the $0.02 per-question cap), or an explicit
waiver permitting continuation. The set is never reduced and no question is
silently dropped. Merging into main and the V0 scoping decision remain human.
