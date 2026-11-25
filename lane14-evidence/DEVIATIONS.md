# Lane 14 — DEVIATIONS.md

Append-only record of anything irregular during the full V0 evaluation.
Phase A entries are recorded now; Phase B will append its own entries after
human approval.

## Phase 0 — preconditions

- **P1 PASS.** `git fetch origin`; `origin/main` tip = `f45a67ce31a9ed1be52200ec77494bdcffd29b2b`.
  `git merge-base --is-ancestor` returned 0 for both `8d4a165e7808d1b5dbca3801048cccb1e34ae180`
  ("qa: verify production hub-ranking path against probe") and
  `8b4a6c116a5553097d84e7a5063a613c0eeea6fd` ("docs: keep AGENTS state refresh within scope").
  Branch `lane/14-v0-eval` created from that exact tip in a fresh worktree
  `C:\Users\mianq\Projects\WikiMind-L14`. Pinned run base recorded.
- **P2 PASS.** `V0_PROTOCOL.md` present; thresholds and question-set requirements quoted
  verbatim in `PROTOCOL-THRESHOLDS.md`.
- **P3 PASS.** Fresh CPython 3.11.15 venv (`C:\Users\mianq\.local\bin\python3.11.exe`),
  `pip install -e ".[dev]"`. `ruff check .` = All checks passed; `ruff format --check .` =
  142 files already formatted; `pytest` = **342 passed, 152 skipped** (exit 0). Raw log:
  `phase0-pytest.txt`.
- **P4 PASS.** `.env` (copied from main worktree, gitignored) points at dev corpus
  `wikimind_l0`, **not** `wikimind_live_l3`. Live corpus is targeted only via the DSN
  override derived at runtime by `live.py` (Ruling 15), with
  `SET default_transaction_read_only = on`.
- **src/, tests/, and frozen files:** `git diff --name-only -- src tests compile/prompts.py
  qa/prompts.py llm/cost.py migrations` is empty. Nothing on this lane measures anything
  other than `main` at the pinned base.

## Phase A — question authoring

- **A1 compliance.** Four source documents' full text read from the document store
  (`documents` + `channels.raw_text`) in `wikimind_live_l3` via `dump_sources.py`, session
  read-only. **No `claims` or `edges` were opened during authoring.** `projects`,
  `documents`, `channels` only. Raw text written to a scratch dir outside the repo and
  treated as untrusted data, never prompt text.
  - `1706.03762-attention`: 37,906 chars / 74 channels
  - `1406.2661-gan`: 28,627 chars / 47 channels
  - `2005.11401-retro`: 68,681 chars / 107 channels
  - `contradiction-note`: 725 chars / 1 channel
- **A2 composition.** V0_PROTOCOL.md *does* specify count and mix, so the work card's
  fallback (60 questions) was **not** used. Authored the specified mix verbatim:
  **40 single-hop + 60 multi-hop + 30 contradiction/aggregation = 130**.
  `author_questions.py` produced `questions.json` with every question carrying a
  grading rubric, as the protocol requires. Questions were drafted from source text and
  the one in-corpus cross-citation (RAG ref [58] → Vaswani et al.) only.
- **Deviation A-1 (additional U group).** A fourth group of **20 deliberately
  unanswerable questions** was added (total 150). Rationale: the Lane 14 work card's
  B5 rubric explicitly requires "unanswerables stay honest"; the protocol says "at least
  130", so an addition is permitted and the specified 40/60/30 mix is preserved verbatim.
  The group is separately labeled in `questions.json` (`bucket: "unanswerable"`) and in
  the answer key. `20/150 = 13.3%`, close to the work card fallback's ~15%.
- **Deviation A-2 (power-check pilot not run).** V0_PROTOCOL.md's "Before freezing,
  compute the real interval from a pilot of 20 questions" was **not** executed. Reasons:
  (a) the protocol's pilot is paired (compile vs a strong-RAG baseline) and **no baseline
  arm exists** in this single-arm evaluation of `wikimind ask`; (b) running the engine
  during authoring would constitute drafting from a system's output, which the protocol
  forbids; (c) the Phase A authoring budget is $0.50 shadow and the work card's A2/A3
  sequence freezes questions before any system run. The frozen set is unchanged and the
  power check is recorded for the Phase B threshold table as **N/A (single arm)**.
- **A3 freeze discipline.** `questions.json` sha256 =
  `bc0fd07c0148bc2452b1bca01aa946e093283967c47b116112b20d97a9648dc4`, computed at freeze
  and re-asserted by `answer_key.py`. Only after that hash was recorded were claims
  consulted. The answer key (`answer_key.json`) classifies each target present/absent/
  conflict and records supporting claim ids and document slugs.
- **Deviation A-3 (corpus absence: 18.4 BLEU / "synthetic" not distilled).** The
  contradiction note's deliberately false statement ("only 18.4 BLEU", "a full 10 BLEU
  below") was **not** distilled into stored claims; only its "improving ... by over 2
  BLEU" line exists as claim `cc28fdac…`. Consequently C011/C019 are `one-sided`
  (expected: cite 28.4 and state honestly that no stored claim supports 18.4) and
  C012/C014 are `insufficient-evidence` (expected: honest insufficiency). This is
  recorded per Ruling 25(2)/(3); **no frozen question text was amended** and the
  expected outcome is "insufficient-with-evidence" instead.
- **Deviation A-4 (corpus absence: several RAG numbers).** Answer-key probes for
  `44.1` (RAG-Token NQ EM), `72.5` (FEVER-3), `42.7` (human factuality) and `626`
  (total trainable params) returned no stored claim, so S030/S031/S032/S035 are
  `insufficient-evidence` (expected: honest insufficiency). Frozen question text is
  unchanged; expected outcomes are marked insufficient-with-evidence per Ruling 25(3).
- **A3 final classification (150 rows):** 111 `present`, 20 `absent` (U group, expected),
  11 `conflict` (both sides in claims), 2 `one-sided`, 6 `insufficient-evidence`.
- **A4 spend.** No LLM calls were made during authoring: the questions were authored
  directly from source text. Isolated ledger `author_costs.jsonl` exists and is empty;
  `wikimind llm costs --path lane14-evidence/author_costs.jsonl` reports
  "No cost events at ...". **Authoring spend = $0.00 shadow, within the ≤ $0.50 budget.**
  `WIKIMIND_COST_DB_MIRROR` was never set.

## Phase B — pending human approval

No Phase B work has run. This lane stops after Phase A per work card A5.
