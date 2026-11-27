# LANE 18 — Runaway generation guard

**Branch:** `lane/18-runaway-guard`
**Base:** `main @ d362783d0c3cd8c825492866bcf87b7311e5cd63` (verified with `git rev-parse`
before the worktree was created; `main` had not moved).
**Date:** 2026-11-27

## What changed

| File | Change |
| --- | --- |
| `src/wikimind/llm/guard.py` | **New.** Cap config resolution, cap validation, the retryable breach error check, and cap-hit cost-event logging. |
| `src/wikimind/llm/errors.py` | Added `OutputCapExceededError` (a retryable `ProviderError`). |
| `src/wikimind/llm/gemini.py` | Cap enforced inside the retryable attempt; usage parsing factored into `_usage_from_response`; provider-side truncation removed. |
| `src/wikimind/llm/judge.py` | Same cap/check/log path for the OpenRouter judge; usage parsing factored into `_parse_response`; provider-side truncation removed. |
| `src/wikimind/llm/__init__.py` | Public exports for the cap constants, config helpers, and new error. |
| `tests/test_llm_output_cap.py` | **New.** 11 keyless fake-client tests. |

**Frozen paths:** `git diff` is empty for `src/wikimind/llm/cost.py`, `migrations/`,
`src/wikimind/qa/`, `src/wikimind/compile/`, and `src/wikimind/webapp/`.
No config file was edited; configuration is wired through the existing env / `.env`
mechanism (`wikimind.config.load_dotenv`).

## Cap design

1. **Limit.** `DEFAULT_MAX_OUTPUT_TOKENS = 4096`, configurable with
   `WIKIMIND_MAX_OUTPUT_TOKENS` (env var or `.env`). Values must be positive integers;
   invalid values fail fast at client construction. An explicit `output_token_cap=`
   constructor argument overrides the environment. The legacy `max_output_tokens`
   (Gemini) / `max_tokens` (judge) constructor arguments are now accepted as aliases
   for this **local** cap and are deliberately **not** forwarded to the provider —
   that removes the only silent-truncation path.
2. **Measurement.** The cap is checked against the provider-reported output tokens:
   `candidates_token_count + thoughts_token_count` for Gemini, and
   `completion_tokens` for the judge. Missing/invalid usage reports zero, so an
   unmeasurable response is not falsely flagged (the existing `usage_invalid` marker
   still records the defect).
3. **Failure semantics.** A breach raises `OutputCapExceededError` from *inside* the
   existing `call_with_retries` loop (both retry predicates now recognise it), so it
   is a **failed attempt** retried under the unchanged exponential-backoff policy.
   No `LLMResult` is produced for that attempt — the oversized text is discarded and
   can never be returned, stored, or graded. After all attempts are exhausted the
   client raises `RetryExhaustedError`.
4. **Logging.** Every failed attempt emits a `CostEvent` through the existing meter
   with the **full** token counts and `meta.output_cap_exceeded = true`,
   `meta.output_cap = <cap>`; caller metadata (e.g. A/B arm tags) is preserved. This
   is "nothing hidden" accounting consistent with Ruling 30: the runaway's real cost
   stays visible even though its answer is discarded. A valid retry is metered
   normally and carries no cap marker.
5. **Boundary.** Exactly-at-cap is accepted; only `out_tokens > cap` fails.
6. **Scope.** Applies to the two generation clients (`GeminiClient` — the `qa.answer`
   drafter — and `JudgeClient`). Embeddings are untouched: `FakeEmbedder` /
   `SentenceTransformerEmbedder` never consult the cap.

Ruling 30 classifies >10,000 output tokens from one `qa.answer` call as an anomaly
and defers prevention to Gate C. The default cap of 4096 triggers earlier than that
threshold; this lane changes no evaluation/corpus code or evidence.

## Tests and quality gates

All new tests use injected fake providers — zero network, zero LLM spend.

```text
ruff check .                         All checks passed!
ruff format --check .                205 files already formatted
pytest (no WIKIMIND_DB)              382 passed, 165 skipped in 50.33s
pytest (sandbox WIKIMIND_DB)         547 passed in 99.28s
```

Baseline on `main` was **371 passed + 165 skipped** (no-DB) and **536 passed** (DB).
The delta is exactly the 11 new tests; no existing test changed behaviour.

Coverage in `tests/test_llm_output_cap.py`: default/configured/invalid cap; cap from
`.env`; strict boundary; cap-hit on oversized output; cap-hit triggers retry with the
existing backoff; cap-hit is logged with full usage and a cap marker; cap-hit
exhaustion raises without returning text; the runaway marker never appears in a
returned result or exception; judge equivalents; provider-side truncation knobs are
not forwarded; embeddings are out of scope.

## Budget and safety

- **LLM spend: $0.00.** Zero real calls; all tests are fake-client unit tests.
- No HTTP 429 occurred (no network calls at all).
- `wikimind_live_l3` was not accessed. `WIKIMIND_COST_DB_MIRROR` was never set.
  The DB suite used only the sandbox database configured in the parent worktree's
  `.env`; no DSN or credential is reproduced here.
- No cost ledger was produced (the gitignored `costs.jsonl` was neither created nor
  committed).

## Evidence

`lane18-evidence/` contains the ruff and pytest output files, the CRLF
`SHA256SUMS.txt`, and `manifest.py`. The manifest generator asserts via `git ls-files`
that every listed path is git-tracked before writing the manifest (Ruling 31, Gate C),
so a `.gitignore` rule can never silently drop an evidence file.
