# LANE 14 — HALT REPORT (Ruling 7, real HTTP 429)

**Status: HALTED. This is NOT a completed V0 evaluation. No verdict table, no
threshold table, no accuracy claim is issued.**

## Halt condition

- Date: 2026-11-26.
- Question: **C020** ("How many distinct EN-DE BLEU numbers are associated with the Transformer across the corpus?"), attempt 1.
- Error: `429 RESOURCE_EXHAUSTED`, HTTP body `code: 429`, quota metric
  `generativelanguage.googleapis.com/generate_content_free_tier_requests`,
  `limit: 500`, quota id `GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
  `retryDelay: 343s`. Genuine Gemini free-tier daily quota exhaustion.
- Action: runner halted immediately, wrote `{"halt": "HTTP 429 — Ruling 7; scope unchanged"}`.
  No further questions were attempted. This follows the work card's "any real HTTP
  429: halt immediately and report" and Ruling 7 ("take the extra days. Never cut
  runs. Never reduce the question set").

## Run state at halt (from `run_manifest.json`, 120 rows)

| Category | Count | IDs |
| --- | ---: | --- |
| Answers obtained (one pass, production defaults) | 107 | all except those below |
| Exhausted 6 bounded attempts on transient 503 | 12 | M023, M056, M058, M059, M060, C001–C007 |
| Halted on real HTTP 429 | 1 | C020 |
| Never attempted | 30 | C021–C030, U001–U020 |

- Total attempts recorded: 222 (max 6 per question; the 12 exhausted rows were not re-attempted after the server-restart resume).
- No retrieval flags were passed; every obtained answer reports production defaults:
  `retrieval: 20 seeds, 150/150 claims (capped)`.
- Isolated answer ledger: `lane14-evidence/costs.jsonl`; `WIKIMIND_COST_DB_MIRROR` unset.
- Corpus proof at halt (`corpus_proof_halt.txt`, read-only session) is identical to the
  before-proof: documents 4, claims 530, edges 1593, compile_units 229, cost_events 1427,
  nodes 861, node_versions 1365, events 0, ledger_blocks 0, jobs 0, same max nodes.created_at.

## Preservation and next step

- Frozen question set SHA256 `bc0fd07c0148bc2452b1bca01aa946e093283967c47b116112b20d97a9648dc4`
  and answer key are **unchanged**.
- No source, test, or frozen-file edits on this lane.
- Await a fresh Gemini quota window (or explicit human direction) before resuming.
  On resumption: run only the 31 unanswered questions (C020 + C021–C030 + U001–U020),
  keeping their existing attempt counts, then complete verification, Ruling 25 judging,
  M3b, threshold table, B7 proof, manifest and report.
- Merging into main and the V0 scoping decision remain human actions.
