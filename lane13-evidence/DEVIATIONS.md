# Lane 13 run deviations and incident record

Append-only record of deviations during the mini-check. No deviation changed the
question set, the pipeline, the prompts, the model pins or the budget caps.

1. **Pre-run smoke question (Q01).** Before the batch was launched, Q01 was run
   once on its own to validate the live path. Its successful result was kept as
   the Q01 final answer and the batch runner skipped it. This is why the first
   batch log line reads `{"id": "Q01", "skip": "already successful"}`.

2. **False-positive 429 detector (corrected mid-run, no effect on answers).**
   The first detector treated the benign Hugging Face warning "unauthenticated
   requests ... set a HF_TOKEN to enable higher rate limits" as a rate-limit
   halt. It briefly flagged Q01's initial failed attempt (a genuine Gemini 503,
   not a 429) before the regex was tightened to HTTP-pattern-only matching.
   No real HTTP 429 occurred at any point; Ruling 7's halt condition was never
   triggered. The false-positive event was not preserved as a separate artifact
   in the original logs; this entry records it.

3. **Persistent transient Gemini 503 "high demand" (schedule stretched).**
   Many `wikimind ask` calls failed after three client-level retries with
   `503 UNAVAILABLE`. The runner added bounded backoff (20/40/60/90/120 s) and
   resumed failed questions on later passes; no question was dropped. Final
   per-question attempt counts are in `run_manifest.json`. Failed provider
   calls emit no cost event, so 47 successful `qa.answer` events correspond to
   31 final answers plus 16 superseded successful attempts out of 173 subprocess
   invocations across `run.log` and `run-retry1.log`.

4. **Retry-pass manifest rewrite.** A single-question resume run wrote a
   manifest containing only that question; the following batch therefore
   re-ran all 31 questions and overwrote their answer files. All re-runs
   succeeded except Q04, which was then answered on a final single-question
   pass. The final `run_manifest.json` contains all 31 rows with exactly one
   successful row per question. This inflated the evidence ledger (409 events)
   but not the corpus `cost_events` (1427 unchanged).

5. **Retrieval operated at a fixed maximal setting.** Every final answer
   reports `20 seeds, 150/150 claims (capped)`; retrieval depth and seed count
   were not varied, so this mini-check does not isolate entry-node-selection or
   traversal quality. Directional scope only (D14).

6. **No secret leakage.** The DSN override was passed as a `--dsn` CLI value and
   never printed into an evidence artifact; `.env` was not copied into the
   evidence directory. `WIKIMIND_COST_DB_MIRROR` was not set, so cost events were
   not mirrored to the live corpus (`cost_events` stayed 1427).
