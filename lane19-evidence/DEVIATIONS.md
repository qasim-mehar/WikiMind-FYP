# Lane 19 — DEVIATIONS.md

Append-only record of anything irregular during the reasoning-repair lane.

## Phase 0 — preconditions

- **P1 PASS.** Base `d362783d0c3cd8c825492866bcf87b7311e5cd63` verified as `origin/main` after
  `git fetch origin`; branch `lane/19-reasoning` created from that exact tip.
- **P2 PASS.** ruff check clean; ruff format clean; pytest DB 536 passed; pytest no-DB 371 passed
  / 165 skipped. Raw logs retained in this directory.
- **P3 PASS.** Frozen-file diff (`compile/`, `llm/cost.py`, `webapp/`, `migrations/`) empty.

## Phase 2 — corpus setup and halt

- **D-1 (no seeded fixture corpus in dev/sandbox).** `wikimind_l0`, `wikimind_l16_sandbox`,
  `wikimind_l17_sandbox`, `wikimind_l8`, `wikimind_l9_w3_12020` and `wikimind` were inspected
  read-only. None contains the four-document fixture corpus (the compiled 530-claim corpus exists
  only in `wikimind_live_l3`, which this lane forbids). Decision: create a fresh sandbox corpus from
  new source documents authored for this lane. This preserves the contamination ban: the probe
  domain is fictional and shares no entities, quantities or questions with the sealed exam.
- **D-2 (transient Gemini 503).** The first compile attempt failed on `503 UNAVAILABLE` (high
  demand) after three transport retries. No 429 at that point; retried.
- **D-3 (MANDATORY HALT — real HTTP 429, Ruling 7).** During a later compile, the OpenRouter judge
  returned `HTTP 429` / `code: 429`, `limit_source=openrouter_free_tier_daily`,
  `X-RateLimit-Limit: 50`, `X-RateLimit-Remaining: 0`, `X-RateLimit-Reset: 1795824000000`.
  Per the work card ("Real HTTP 429 → STOP immediately") and Ruling 7, the lane halted at that
  point. No further LLM call was made. The bounded retry loop in `setup_corpus.py` consumed the
  remaining free-tier judge quota; this is recorded as an error in lane planning, not a waiver.
- **D-4 (probe work not reached).** Probe authoring, SHA256 freeze, baseline, repair and post-repair
  measurement were not performed. No accuracy numbers are claimed. `LANE19-HALT-REPORT.md` records
  the exact state and spend. Resume requires governance direction.
