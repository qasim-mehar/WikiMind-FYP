# Lane 13 — Directional mini-check report (D14)

**Branch:** `lane/13-mini-check` (worktree `..\WikiMind-L13`, from `origin/main` `f45a67c`)
**Corpus:** `wikimind_live_l3`, project `v0-fixtures`, targeted **only** by DSN override
**Status:** COMPLETE — 31/31 questions answered and judged; STOP before merge (human action)

---

## 1. Preconditions and setup

| Check | Result |
| --- | --- |
| `git fetch origin` | done |
| `8d4a165` (lane/11-qa-recall) ancestor of `origin/main` | ✅ `git merge-base --is-ancestor` = 0 |
| `8b4a6c1` (lane/12-removal) ancestor of `origin/main` | ✅ = 0 |
| CI green on main tip | ✅ CI **#47** on `f45a67c` — "completed successfully" |
| Worktree / branch | `..\WikiMind-L13`, `lane/13-mini-check` from `origin/main` |
| Interpreter | **CPython 3.11.15** (uv-managed), venv verified explicitly; `sys.executable` = `.venv\Scripts\python.exe` |
| `.env` | copied from parent, confirmed pointed at **`wikimind_l0`**, gitignored; never edited for corpus access |
| Green baseline | `ruff check .` all passed; `ruff format --check .` 142 files formatted; `pytest` **342 passed, 152 skipped** |

No migrations, no prompt or model changes, no recompilation, no force-push, no merge into main.

## 2. Corpus-count proof (read-only `wikimind_live_l3`)

| Table | Before | After | Identical |
| --- | --- | --- | --- |
| documents | 4 | 4 | ✅ |
| claims | 530 | 530 | ✅ |
| edges | 1593 | 1593 | ✅ |
| compile_units | 229 | 229 | ✅ |
| cost_events | 1427 | 1427 | ✅ |

Counts were taken with a helper that forces `default_transaction_read_only = on`. `db reset` was never invoked. A supplementary independent read-only check covered the other write-prone tables: nodes 861, node_versions 1365, events 0, ledger_blocks 0, jobs 0; maximum `nodes.created_at` = 2026-11-18 (pre-run), so no node was created during the mini-check. The evidence cost ledger was isolated to `lane13-evidence/costs.jsonl`; `WIKIMIND_COST_DB_MIRROR` was not set, so no cost row was written to the corpus.

## 3. Question set

31 questions were derived from **stored claims via read-only SQL** (not from the papers): 27 corpus-verifiable questions spanning attention (10), GAN (7) and RAG (10); 2 conflict questions targeting the two recorded `contradicts` edges (Transformer En-Fr BLEU 41.0 vs 41.8; the two conflicting R3 author lists); and 4 deliberately unanswerable questions to exercise honest insufficiency. See `lane13-evidence/questions.json` and `digest-utf8.txt`.

## 4. Execution

- Real `wikimind ask` CLI, `WIKIMIND_LIVE=1`, real drafter `gemini-3.1-flash-lite`, pinned path, default claim cap 150, defaults for seeds/hops.
- Every call metered; no pipeline change mid-run.
- **No HTTP 429 occurred**, so the Ruling 7 halt was never triggered. Many calls hit transient Gemini **503 "high demand"**; the runner retried with bounded backoff (20/40/60/90/120 s) and resumed failed questions on later passes. Total attempts per final answer are recorded in `run_manifest.json`. The schedule stretched; scope never shrank — all 31 questions were answered.
- No question exceeded the **$0.02 per-question** shadow cap (max $0.00303825), so no overrun needed recording.

## 5. Per-question verdict table (Ruling 25)

"Citations" is prose refs / verified list refs. Every display ref was resolved against a stored claim id and its document slug was cross-checked (`verification.json`): **0 unresolved refs, 0 slug mismatches, 0 unverified prose refs**.

| Q | Target kind | Verdict | Citations (prose / list) | Shadow (USD) | Evidence |
|---|---|---|---|---|---|
| Q01 | sufficient | SUFFICIENT | 1 / 1 | 0.00270425 | Transformer big WMT14 En-De BLEU 28.4 [retries: 3] |
| Q02 | conflict | SUFFICIENT — both sides cited | 2 / 2 | 0.00288275 | BLEU 41.0 vs 41.8 on WMT14 En-Fr (contradicts edge) [retries: 5] |
| Q03 | sufficient | SUFFICIENT | 2 / 2 | 0.00262300 | 8 parallel attention heads, dk=dv=64 |
| Q04 | sufficient | SUFFICIENT | 4 / 4 | 0.00288900 | sine and cosine functions of different frequencies |
| Q05 | sufficient | SUFFICIENT | 3 / 3 | 0.00272775 | Adam beta1 0.9, beta2 0.98, epsilon 1e-9 |
| Q06 | sufficient | SUFFICIENT | 3 / 3 | 0.00279100 | self-attn O(n^2 d); recurrent O(n d^2); conv O(k n d^2) [retries: 4] |
| Q07 | sufficient | SUFFICIENT | 2 / 2 | 0.00261225 | encoder 6 layers, decoder 6 layers [retries: 6] |
| Q08 | sufficient | SUFFICIENT | 1 / 1 | 0.00251900 | softmax(QK^T/sqrt(dk))V |
| Q09 | sufficient | SUFFICIENT | 1 / 1 | 0.00254200 | single-head 0.9 BLEU below optimal |
| Q10 | sufficient | SUFFICIENT | 5 / 5 | 0.00292125 | minimax two-player game; G vs D |
| Q11 | sufficient | SUFFICIENT | 1 / 1 | 0.00250450 | D*G(x)=pdata/(pdata+pg) |
| Q12 | sufficient | SUFFICIENT | 2 / 1 | 0.00255875 | global minimum C(G)=-log(4) iff pg=pdata |
| Q13 | sufficient | SUFFICIENT | 1 / 1 | 0.00248325 | MNIST, Toronto Face Database, CIFAR-10 |
| Q14 | sufficient | SUFFICIENT | 1 / 1 | 0.00245500 | maximize log D(G(z)) gives stronger early gradients |
| Q15 | sufficient | SUFFICIENT | 2 / 2 | 0.00259175 | no Markov chain needed for sampling |
| Q16 | sufficient | SUFFICIENT | 3 / 3 | 0.00273450 | Gaussian Parzen window fit to generated samples for test log-likelihood |
| Q17 | sufficient | SUFFICIENT | 4 / 4 | 0.00303825 | RAG-Token per-token latent doc; RAG-Sequence one latent doc |
| Q18 | sufficient | SUFFICIENT | 5 / 4 | 0.00288175 | parametric seq2seq; non-parametric dense Wikipedia vector index |
| Q19 | sufficient | SUFFICIENT | 2 / 2 | 0.00279525 | 110M BERT-base query encoder; 406M BART-large generator |
| Q20 | sufficient | SUFFICIENT | 2 / 2 | 0.00279925 | NQ, WebQuestions, CuratedTrec SOTA |
| Q21 | sufficient | SUFFICIENT | 1 / 1 | 0.00266525 | RAG-Sequence 44.5 EM on Natural Questions |
| Q22 | sufficient | SUFFICIENT | 2 / 2 | 0.00276200 | retrieval collapse: generator ignores docs, equals BART |
| Q23 | sufficient | SUFFICIENT | 2 / 2 | 0.00277650 | gold article in top-10 90%; top-1 71% |
| Q24 | sufficient | SUFFICIENT | 1 / 1 | 0.00250675 | 21 million 728-dimensional vectors |
| Q25 | sufficient | SUFFICIENT | 2 / 2 | 0.00276000 | retrieval index hot-swappable without retraining |
| Q26 | conflict | SUFFICIENT — both sides cited | 2 / 2 | 0.00296700 | two conflicting R3 author lists (contradicts edge) |
| Q27 | sufficient | SUFFICIENT | 1 / 1 | 0.00268850 | contradiction-note claim: >2 BLEU improvement on WMT14 En-De |
| Q28 | insufficient | HONEST INSUFFICIENCY | 0 / 0 | 0.00246675 | no inference-latency claim in stored claims |
| Q29 | insufficient | HONEST INSUFFICIENCY | 3 / 3 | 0.00269875 | Adam beta/eps present, weight-decay absent |
| Q30 | insufficient | HONEST INSUFFICIENCY | 0 / 0 | 0.00236825 | V100 GPUs belong to RAG, not GAN |
| Q31 | insufficient | HONEST INSUFFICIENCY | 1 / 1 | 0.00265375 | corpus says December 2018 Wikipedia dump (premise corrected) |

**31/31 PASS under Ruling 25.** Notes:
- Q02 and Q26 each present both recorded sides with a citation per side — no silent winner.
- Q28/Q29/Q30 return `sufficient: false` with no fabricated answer; claim-absence evidence is in `absence.json` (e.g. `latency`/`millisecond`/`weight decay` = 0 hits; the sole V100 claim belongs to RAG/BART, not GAN).
- Q31 asked about a "December 2019" dump that does not exist; the engine answered with the stored "December 2018" claim rather than fabricating. The prose is accurate and cited; the `sufficient: true` flag is optimistic for a false-premise question — recorded as an observation, not a failure.

## 6. Ledger delta totals and budgets

| Measure | Value | Budget | Result |
| --- | --- | --- | --- |
| Final-answer shadow total (31 answers) | **$0.08336800** | ≤ $2.00 | ✅ |
| Max single final answer | **$0.00303825** (Q17) | ≤ $0.02 | ✅ |
| Evidence ledger events (all attempts) | 409 | — | — |
| — `embed.query` calls | 362 (local, $0 shadow) | — | — |
| — `qa.answer` calls | 47 (incl. retries/failures), $0.12610175 shadow | — | — |
| Evidence ledger shadow total | **$0.12610175** | ≤ $2.00 | ✅ |
| Corpus `cost_events` delta | 0 (1427 → 1427) | 0 | ✅ |

The ledger total exceeds the final-answer total because failed/retried attempts still emit metered embed events and successful-but-superseded answers; all real cost is in the isolated evidence file.

## 7. Directional read

The engine answers end-to-end well enough to **schedule the full V0 evaluation**. Across 31 corpus-derived questions it produced correct, machine-verified, citation-bearing prose in every case, surfaced both sides of both recorded contradictions instead of silently choosing a winner, and refused to fabricate on four questions whose targets are demonstrably absent from the corpus — including one false-premise question it corrected against a stored claim. The answer path stayed within a small fraction of both cost gates. The caveats are directional, not disqualifying, but must be carried forward: **single-shot reliability was poor** — roughly three quarters of the 173 CLI invocations failed on transient Gemini 503 "high demand" and every final answer depended on the bounded retry wrapper, so the 31/31 result is an eventual-success rate, not a first-try rate; retrieval ran at a single maximal setting (20 seeds, 1 hop, 150/150 claims capped), so entry-node selection and traversal quality were not exercised as variables; one unanswerable question was flagged `sufficient: true` while correctly amending a false premise; and the sample is small and hand-derived from the same claims it tests, so it cannot detect systematic recall gaps of the kind Ruling 26 recorded for q05. No criterion here exercises the full protocol's CI thresholds, which remain gated to the complete run. Recommendation: proceed to the full evaluation under `V0_PROTOCOL.md`, carrying the q05 hub-recall blocker, the sufficiency-flag observation and the provider-reliability/retrieval-variable caveats into that run as tracked risks.

## 8. Runtime evidence and manifest (Ruling 18)

All answers, logs, ledger, scripts, the deviation record and derived digests are in the **untracked** directory `lane13-evidence/`; nothing was archived into the repo. `SHA256SUMS.txt` lists 184 files with their SHA-256 digests and every digest was verified by the quality pass.

- Evidence directory: `lane13-evidence/`
- Manifest: `lane13-evidence/SHA256SUMS.txt`
- Manifest SHA-256: **`88d676ede0e0e029f01b8e9b35503d239cec221764b738d700ee304b61f82491`**

## 9. Quality pass (Ruling 16)

Read-only subagent review completed. Overall verdict: **PASS with caveats** — a clean, internally consistent, demonstrably zero-write mini-check with faithful Ruling 25 judging; retry-dependent reliability, an incomplete early-run record (now remedied by `lane13-evidence/DEVIATIONS.md`) and fixed maximal retrieval limit the strength of the directional read. The subagent fixed nothing.

| Severity | Finding |
| --- | --- |
| **Medium** | High transient failure rate: ~129 of 173 CLI invocations failed, mostly Gemini 503 "high demand"; the 31/31 result depends on the 6-attempt retry wrapper, so single-shot reliability was poor. Reflected in §7. |
| **Low** | Mid-run 429-detector fix left no artifact of the initial false positive (HF "higher rate limits" warning) or the pre-batch Q01 smoke run. Remedied by deviating record `DEVIATIONS.md` items 1–2. |
| **Low** | Retrieval not exercised as a variable — every final used 20 seeds / 150/150 capped claims, so sufficiency may reflect maximal context rather than entry-node selection. Recorded as `DEVIATIONS.md` item 5 and in §7. |
| **Low** | The offline verifier resolves refs against all 530 stored claims rather than the 150 supplied to the model; Ruling 21's in-path check is still performed by `qa/verify.py` (no unverified removals in any final), so this is supplementary, not a breach. |
| **Info** | No FORBIDDEN violation and zero corpus writes: `git status` shows only the two untracked evidence/report paths; frozen docs, migrations, prompts and pins untouched; no db reset, recompile, force-push or merge. Independent check of nodes 861 / node_versions 1365 / events 0 / ledger_blocks 0 / jobs 0 confirms §2. |
| **Info** | Questions are genuinely claim-derived (`digest-utf8.txt`); both recorded `contradicts` edges are targeted by Q02/Q26; Q28–Q31 absent/false-premise cases backed by a 530-claim scan. |
| **Info** | Ruling 25 judging faithful: 0 unresolved refs, 0 slug mismatches, 0 unverified prose refs; conflicts cite both sides; Q31's `sufficient: true` is defensible because the corpus verifiably contains the Dec-2018 claim (Ruling 25(2)/(3)). |
| **Info** | Costs and manifest internally consistent: final sum $0.083368, max $0.00303825, ledger 409 events / $0.12610175, 184 manifest entries all verifying. Latent risk noted: `--dsn` also feeds `build_meter`, so an ambient `WIKIMIND_COST_DB_MIRROR=1` would mirror costs to the live corpus — it was not set here. |
| **Info** | No secret leakage in evidence files; `__pycache__` was removed before the final manifest was generated. |

## 10. STOP

Merging into main and pushing main are human actions (Ruling 28). This lane terminates here; no merge, no force-push, no further writes to the live corpus.
