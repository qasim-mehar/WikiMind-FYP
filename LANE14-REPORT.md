# LANE 14 — FULL V0 EVALUATION (single-arm, two-window evidence run)

## 1. Pinned base and scope

- Pinned run base: `f45a67ce31a9ed1be52200ec77494bdcffd29b2b` (ancestors 8d4a165, 8b4a6c1 confirmed).
- Branch `lane/14-v0-eval`; worktree `C:\Users\mianq\Projects\WikiMind-L14`.
- Live corpus `wikimind_live_l3` targeted only by DSN override, session forced `default_transaction_read_only = on`.
- `src/`, `tests/`, and all frozen files unchanged on this lane.
- Single arm: real `wikimind ask` CLI, `WIKIMIND_LIVE=1`, production retrieval defaults (no forced settings).

## 2. Windows

- Window 1 halt: 2026-11-25T23:54:19-08:00 (C020 real HTTP 429, Ruling 7).
- Window 2 resume: 2026-11-26T00:32:15-08:00 after midnight-Pacific quota reset.
- W2 scope: 31 unanswered (C020 + C021-C030 + U001-U020) plus 12 reopened (M023, M056, M058-M060, C001-C007) = 43 questions.

## 3. Question set and answer key

- Frozen question SHA256: `bc0fd07c0148bc2452b1bca01aa946e093283967c47b116112b20d97a9648dc4`; 150 questions (40 S / 60 M / 30 C / 20 U).
  - Note: "30 C" = 22 contradiction + 8 aggregation questions (the frozen `questions.json` buckets `contradiction` and `aggregation`; matches the per-bucket denominators 9/22 and 0/8 in §7).
- Full set and key: `lane14-evidence/questions.json`, `answer_key.json`, `PHASE-A-REPORT.md`. Unchanged.
- Authoring spend $0.00.

## 4. Corpus-count proof (read-only session)

| Table | Before | After |
| --- | ---: | ---: |
| documents | 4 | 4 |
| claims | 530 | 530 |
| edges | 1593 | 1593 |
| compile_units | 229 | 229 |
| cost_events | 1427 | 1427 |
| nodes | 861 | 861 |
| node_versions | 1365 | 1365 |
| events | 0 | 0 |
| ledger_blocks | 0 | 0 |
| jobs | 0 | 0 |
| max_nodes_created_at | 2026-11-18 08:01:38.064878+05:00 | 2026-11-18 08:01:38.064878+05:00 |

## 5. Run, cost, attempts

- Answered: 150/150; unanswered after both windows: 0.
- Attempt histogram (total attempts per question, both windows): {1: 106, 2: 25, 3: 3, 4: 2, 5: 1, 6: 1, 7: 12}.
- Total attempt wall time (excludes backoff sleeps): 24399.9 s (6.78 h).
- Retrieval lines reported: {'20 seeds, 150/150 claims (capped)': 146, '20 seeds, 104/150 claims': 1, '20 seeds, 106/150 claims': 1, '23 seeds, 150/150 claims (capped)': 1, '24 seeds, 150/150 claims (capped)': 1} (production defaults).
- Answer ledger graded (Ruling 30, anomalies excluded): $0.408898500000; all-inclusive (real spend): $0.509645250000.
- Anomaly spend: $0.100746750000 across 1 event(s); cap $0.50 = PASS.
- M3b judge ledger: $0.037097400000; combined all-inclusive spend: $0.546742650000 (cap $5.00).
- `WIKIMIND_COST_DB_MIRROR` unset throughout; ledger isolated; `cost_events` unchanged.

## 6. Judging (Ruling 25 rubric against frozen key)

- Conservative accuracy (all 150; unanswered = FAIL): 78/150 = 0.5200.
- Engine-capability accuracy (answered only): 78/150 = 0.5200.
- Machine citation verification: `verification.json` (resolution, slug, anchor containment, prose refs).
- Non-Gemini judge sample (M3b): 40 judgments, rate=0.825.

## 7. Threshold table

# Lane 14 — V0 threshold table (single-arm, two-window evidence run)

## Accuracy views

- Conservative (all 150; unanswered counted FAIL): **0.5200** (78/150), 95% bootstrap CI 0.4400–0.6000.
- Engine-capability (answered only; provider exhaustion separate): **0.5200** (78/150), 95% bootstrap CI 0.4400–0.6000.
- Answered: 150; unanswered (provider exhaustion in both windows): 0; halt_429 rows: 0.
- Provider-exhausted rows (exit != 0, non-429): 0.
- Per-bucket (conservative denominators over 150 question IDs): aggregation=0/8, contradiction=9/22, multi-hop=18/60, single-hop=32/40, unanswerable=19/20
- Verdict counts: {'PASS': 78, 'FAIL': 72}.

## Cost accounting (Ruling 30 — two views, nothing hidden)

- Graded (anomalies excluded): $0.408898500000 total, $0.00272599/answered query.
- All-inclusive (real spend): $0.509645250000 total, $0.00339764/answered query.
- Anomaly spend: $0.100746750000 across 1 event(s) (cap $0.50: **PASS**).

## Attempts and wall time (both windows)

- Attempt histogram (total attempts per question): {1: 106, 2: 25, 3: 3, 4: 2, 5: 1, 6: 1, 7: 12}.
- Total recorded attempt wall time: 24399.9 s (6.78 h), excluding backoff sleeps.

## Threshold table

| Metric | Measured value | Reason / basis | Result |
| --- | --- | --- | --- |
| M1 — Paired accuracy difference (multi-hop + contradiction) | N/A | single-arm run; no strong-RAG baseline arm exists to pair against | **N/A** |
| M2 — Paired accuracy difference (single-hop) | N/A | single-arm run; no paired difference / lower bound L computable | **N/A** |
| M3a — Anchor validity (machine citation verification) | 1.0000 of cited claims anchored; 1.0000 of 117 citing answers verified (33 zero-citation answers: 17 graded FAIL, 16 honest-insufficient PASS (no vacuous passes)) | Ruling 25 machine verification is the M3a check (resolution + slug + anchor containment) | **PASS** |
| M3b — Entailment faithfulness (non-Gemini blind judge) | 0.8250 (40 judgments) | judge sample via pinned non-Gemini OpenRouter model | **FAIL(80-90%)** |
| M4 — Amortized cost per paper (40-50 papers, drills, curve) | N/A | corpus is 4 fixture documents; no 40-50 paper corpus, removal drill or repair pass | **N/A** |
| M5 — Cost per query | graded $0.00272599 mean (total $0.40889850); all-inclusive $0.00339764 mean (total $0.50964525) | gate <= $0.02; target <= $0.01. Graded excludes Ruling 30 anomalies; all-inclusive is real spend | **PASS** |
| M6 — Propagation errors on hand-built oracle | N/A | no hand-built oracle / 5 removal drills were run in this single-arm evidence pass | **N/A** |
| M7 — Interviews confirming pain | N/A | no interviews were conducted in this run | **N/A** |
| M8 — Share of L2 content reducing to L1 claim IDs | N/A | corpus has 0 ledger_blocks; no L2 ledger content to measure | **N/A** |

## Decision rule

M1 is the outcome-statistic gate and is N/A in a single-arm run, so no WIN/PARITY/INCONCLUSIVE/LOSS row is selected. This run is directional evidence only (D14); the full two-arm evaluation remains ahead.

Gate note: M5 target ($0.01) met; gate ($0.02) met.


## 8. Deviations

See `lane14-evidence/DEVIATIONS.md` (Phase A A-1..A-4; Phase B B-1..B-18).

## 9. Evidence manifest

- `lane14-evidence/SHA256SUMS.txt` (CRLF, Lane 13 procedure).
- Manifest SHA256: `dbaeae79a16b82caee3ffa781d681d5a7929cc48855f310379cbfd6256cea1cc`.
- Standing rule (Gate C, Ruling 31): the manifest generator MUST assert that every listed path is git-tracked (`git ls-files`) before writing `SHA256SUMS.txt`, so a pre-existing `.gitignore` rule can never silently drop an evidence file again. Recorded here as a deferred rule; no generator code is changed on this doc-only patch.

## 10. STOP

Merging into main and the V0 scoping decision are human actions. No merge, no main push, no further corpus writes.
