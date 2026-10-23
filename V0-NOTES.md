# V0-NOTES — Compendium Phase 0 record

UTC: 2026-10-16. This file is the deviation and ruling record for V0. It is append-only during V0.

## Phase 0 blocker history and resolutions

1. **No git repository** — `C:\Users\mianq\Projects\WikiMind` contained the two frozen documents but no `.git`; no other repo contained the three documents. Resolution: Ruling 1 authorized `git init -b main` at this path. The folder name (WikiMind) is a working title; product naming remains open (PROJECT.md §17 D3).
2. **PROJECT.md missing** — the canonical file had never reached the workspace. Resolution: the original author re-issued the canonical v0.1 text in-session and authorized the agent to save it verbatim (receiving canonical content, not reconstruction). Saved at repo root; verification evidence in the H0 gate report.
3. **Required §2 string missing** — "M3b between 80% and 90% is repairable" was absent from V0_PROTOCOL.md (grep `repairable`: 0 matches). Resolution: Ruling 3, Option A — authorized amendment before the freeze (record below).
4. **Parameter table unfilled** — resolution: Rulings 4–5 filled it (see Model stack below).

## M3b amendment record (Ruling 3, Option A)

- **What was inserted** (verbatim, single sentence): "M3b between 80% and 90% is repairable, like M3a: fix the system, then re-run on fresh questions. Below 80% remains the unconditional kill."
- **Where**: V0_PROTOCOL.md, Overrides paragraph, immediately after "If M3b is below 80%, the result is KILL whatever else passes." Nothing else in the document was changed.
- **Why**: a diagnosed rule bug, not intent. As written, M3b = 85% with a WIN result kills via the integrity column, while the same 85% with a PARITY/INCONCLUSIVE result gets a repair path — an asymmetry that also made the "below 80% is a kill" override redundant. Freezing it would let a bug kill the project instead of a finding.
- **Source**: final review finding, previously unapplied (the Phase 0 work order had never been executed).
- **Race check**: nothing was frozen when the amendment was applied — no commit, no tag, no corpus, no questions, no results. Rules-before-results still holds; this is finalization, not tampering.
- **Authorizing human**: Qasim (Ruling 3, this session). UTC date: 2026-10-16.
- **Edit window closed**: this was the last legitimate edit. After tag `v0-preregistered` exists, the no-edit rule is absolute until the V0 decision table is applied.

## Model stack — Ruling 5 (free tier, frozen in this pre-registration)

| Parameter | V0 value | Key |
| --- | --- | --- |
| REPOSITORY_ROOT | C:\Users\mianq\Projects\WikiMind | — |
| DISTILL_MODEL | gemini-2.5-flash (AI Studio free tier) | GEMINI_API_KEY (free; aistudio.google.com; needed by Week 2 only) |
| ANSWER_MODEL | gemini-2.5-flash — identical for both arms | same key |
| JUDGE_MODEL | mistral-small-latest (Mistral free tier; family ≠ Gemini ✓) | MISTRAL_API_KEY (free; console.mistral.ai; Week 2 only) |
| EMBED_MODEL | local sentence-transformers, BAAI/bge-small-en-v1.5 — in-process, no key, no limits | none |
| BUDGET_CAP | $100 on actual spend (expected ≈ $0) | — |
| GROBID_URL | http://localhost:8070 | — |

Constraints that survive every substitution:

- Both arms use the SAME answer model, unchanged for the entire V0 run.
- The judge comes from a DIFFERENT model family than the answer model (Mistral ≠ Gemini ✓).
- Every LLM call goes through the cost meter — free calls included; tokens logged even when price is $0.
- The embedding model is IDENTICAL for both arms (Arm A's vector index and Arm B's entity matching use the same embedder).
- The stack freezes before tuning starts (H1 report includes final model IDs). No model changes after tuning begins; none at all after the frozen run starts.

Pre-authorized fallbacks (record which is used when invoked):

- If the Mistral free tier is unavailable: the judge may be any OpenRouter `:free` model from a non-Gemini family (deepseek, qwen, or llama).
- If an exact Gemini ID is unavailable: substitute the current flash-class equivalent.
- If the judge family must change beyond the pre-authorized fallbacks, the human decides — not the agent.

Optional upgrade (human decision, before tuning only): an OpenRouter $10 credit lifts free-model daily caps and permits a deepseek-class judge. Ruled once, never mid-run.

## Approved deviations

1. **Pro-class answerer waived for V0.** The master prompt specified a Pro-class answer model; no free Pro-class exists at the needed volume. Comparison fairness is preserved because both arms share the identical model (gemini-2.5-flash). A weaker answer model may compress M1 differences — this must be noted in the Week 3 diagnosis if the result is INCONCLUSIVE.
2. **Judge/embedding ruling superseded.** The earlier gpt-4.1 / text-embedding-3-small ruling is superseded for V0 by the Ruling 5 stack (originally proposed because an OpenAI key was available, verified absent, and no Anthropic key exists either).
3. **M3b band inserted into V0_PROTOCOL.md pre-freeze** (master prompt §2 expected it present; it was missing at first §2 check). Record above.

## Cost accounting — Ruling 6 (shadow cost)

With a free-tier stack, actual spend ≈ $0, and M4/M5 would pass vacuously on actual cost. For Week 3, cost is computed two ways:

- **Actual cost** (≈ $0) — reported.
- **Shadow cost** = tokens × list prices of the production stack: distill-role calls at Flash-class list price, answer-role calls at gemini-2.5-pro list price, judge at its list price.

The M4/M5 gates (≤ $0.08 per paper / ≤ $0.02 per query; targets $0.04 / $0.01) apply to the **shadow cost**, so the gates answer the question they exist to answer: would this architecture be sellable at production pricing? Every LLM call is metered regardless (model, tokens in/out, cost including $0, purpose, timestamp → costs.jsonl).

## Rate limits — Ruling 7 (schedule, not scope)

Before the Week 2 build: verify current rate limits for each ruled model, record them in this file, and build retry-with-backoff into the API layer. If limits stretch the 3-run schedule: take the extra days. Never cut runs. Never reduce the question set. Deviations get recorded, per the protocol.

## Keys

Keys are NOT needed for the freeze; both free keys (GEMINI_API_KEY, MISTRAL_API_KEY) can be created any time before Week 2. Environment variables only; never committed; never logged. Current environment state checked 2026-10-16: no GEMINI/GOOGLE/GENAI, MISTRAL, OPENAI, or ANTHROPIC keys present — flagging now so both free keys are provisioned before Week 2.

## PROJECT.md transport notes

- The canonical v0.1 text was transmitted with formatting flattened (header marks, table pipes, code fences, and ASCII diagrams lost; words merged at hard-wrap points). The agent transcribed it with all content preserved and structure restored (markdown headings, tables, code fences, de-merged words, diagrams rebuilt to the same component boxes). Any residual formatting artifact is cosmetic; content is verbatim.
- One determinate glyph in the Changelog line ("LLM Wiki⟨?⟩OKF synthesis") was indeterminate after transport and is rendered as "LLM Wiki–OKF synthesis".
- Known cosmetic wart preserved verbatim: §1 "Zotero/Paperplug" (typo for Paperpile; to be corrected in the v0.2 rewrite, not now).
- Ruling 2 verification checklist is attached to the H0 gate result.

## Branch plan (post-freeze)

Phase 0 commits land on `main`. All V0 work after the freeze goes on `qasim/v0-execution`, branched from tag `v0-preregistered`. The human pushes after each gate; the agent never pushes.

## Ruling 8 — Judge family amendment (recorded at the top of the execution branch)

Supersedes the Mistral line of Ruling 5 (pre-authorized fallback case). UTC: 2026-10-16. Branch: qasim/v0-execution, first commit.

- **Keys**: the human keeps exactly two keys — GEMINI_API_KEY and OPENROUTER_API_KEY. No Mistral key will be provisioned.
- **JUDGE_MODEL**: an OpenRouter `:free` model from a NON-Gemini family — DeepSeek-class preferred (e.g. deepseek/deepseek-chat:free); Qwen- or Llama-class acceptable if the DeepSeek free variant is unavailable. The exact current ID is verified at build time and recorded here.
- **Unchanged**: DISTILL_MODEL gemini-2.5-flash; ANSWER_MODEL gemini-2.5-flash identical for both arms; EMBED_MODEL local BAAI/bge-small-en-v1.5 (no key); BUDGET_CAP $100; GROBID_URL http://localhost:8070.
- **Binding constraints unchanged**: same answer model across both arms; judge family ≠ answer family; every LLM call through the cost meter (tokens logged even at $0); stack frozen before tuning; no model changes after the frozen run starts.
- **Rate-limit reality (Ruling 7 applies — verify and record actual limits before Week 2)**: OpenRouter `:free` models carry low daily caps without purchased credit (roughly 50 requests/day) and higher caps after a one-time $10 credit (roughly 1000/day). The judge workload is ~780 grading calls plus entailment checks — potentially 1500–2000 calls. Without the credit, judging alone stretches across weeks. Either way the schedule takes the hit, never the scope.
- **OPEN DECISION (human, before Week 2)**: the one-time $10 OpenRouter credit. Usage of free models stays $0 with or without it. The agent will record whichever is chosen — never decided by the agent.

## OKF repository check (O2) — CLOSED

Checked UTC 2026-10-16 (~10:05Z). Evidence: fetched repository page + `api.github.com/repos/GoogleCloudPlatform/open-knowledge-format/commits/main`.

- Canonical repo: https://github.com/GoogleCloudPlatform/open-knowledge-format — LIVE (public; 980 stars / 65 forks / 19 watchers; 22 open issues, 7 open PRs).
- License: Apache-2.0 (LICENSE.md, confirmed on the repository page).
- Spec version pinned for V0: **v0.2** (SPEC.md; README advertises "v0.2 specification").
- Last activity at check: commit `ad30107c31c06aec8a7d5636e0d1058118604e6f`, 2026-08-21T20:08:36Z, signed and verified — "Merge pull request #6 … Make every timestamp an ISO 8601 datetime with an explicit offset". 6 commits total on main.
- Build consequences: (1) every timestamp emitted by V0 code uses ISO 8601 datetimes with explicit UTC offset, per the pinned spec; (2) OKF format code is isolated in a single module, per PROJECT.md §3. Note: PROJECT.md v0.1 frontmatter examples show bare dates (`stale_after: 2027-06-12`); the pinned spec supersedes that cosmetic detail at build time. No PROJECT.md edit — it is frozen.
- Pin statement: V0 builds against OKF spec v0.2 as of commit `ad30107`; the format module will carry this pin string.

### Ruling 9 — V0 gate retired; build order reversed (2026-10-24, D14)

D14 amends D1: the engine is built first; measurement comes after. V0
as a pre-build gate is retired. The pre-registered commit and tag
v0-preregistered stay as they are. V0_PROTOCOL.md is unchanged; it is
the spec for the full evaluation that runs on the finished engine.
Before that full run, a mini-check (~30 questions) runs against a
strong hybrid-search baseline once the engine answers end-to-end. It
uses the protocol's metrics. It is a directional smoke test only. The
decision table's CI thresholds apply only to the full run.
Authorized by Qasim, 2026-10-24.
