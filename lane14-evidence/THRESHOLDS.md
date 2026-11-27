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
