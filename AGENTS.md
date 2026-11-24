# AGENTS.md — Agent onboarding for WikiMind / Compendium

> **Audience:** human engineers, coding agents (Codex, Claude Code, OpenCode, Cursor, etc.), and
> agents that will eventually operate a live instance. This is the single "start here" file.
>
> **Purpose:** give any agent enough context to make correct changes on the first day — what the
> project is, which documents are authoritative, what is already built, what is forbidden, and how
> to verify work.
>
> **Normative language:** MUST / MUST NOT / SHOULD / MAY follow RFC 2119. Vocabulary is
> load-bearing: the terms in §5 have exact meanings and MUST be used consistently in code, schemas,
> prompts, docs, and generated content.

---

## 0. Read this first — the orientation trap

This repository contains **two different products in its documentation history**. Nearly every
wrong turn an agent can take comes from confusing them.

| | **Active project (this repo)** | **Legacy prototype (stale doc)** |
| --- | --- | --- |
| Name | WikiMind, product concept **Compendium** | "WikiMind AI" |
| Document | `README.dev.md`, `DECISIONS.md`, `PROJECT.md`, `docs/contracts/`, `V0-NOTES.md` | `README.md` (root) |
| Stack | Python 3.11 library + Typer CLI + PostgreSQL 18 + pgvector | Next.js 16 / React 19 / TypeScript / MongoDB / OpenRouter |
| Data model | versioned knowledge **graph**: nodes, edges, claims, ledgers, passages | documents, chunks, entities, facts, relationships, memory |
| Build stage | Stage 1 engine (CLI/library only — **no UI, no auth, no graph view**) | deployed web app with chat |
| Status in this checkout | **Actually implemented** | **Not present** — no `app/`, no `package.json`, no Next.js code here |

**Rule:** `README.md` is a historical artifact of an earlier prototype. It is **not** a description
of the code in this checkout. Do not add Next.js code, MongoDB drivers, chunking, or `app/` paths
because `README.md` mentions them. When `README.md` and the engineering docs disagree, the
engineering docs win. If you are asked to "fix the web app," stop and clarify — that work is not in
this repository.

---

## 1. Document authority (read in this order)

When documents conflict, the order below decides. Never treat all `.md` files as equal.

1. **`DECISIONS.md`** — the governing decision log (D1–D14). It explicitly supersedes `PROJECT.md`
   on every item it lists. A decision changes **only** through a new entry here that names the
   entry it replaces. This is the first file to read before any design question.
2. **`docs/contracts/*.md`** — frozen interface contracts: `schema.md` (database),
   `parsed-document.md` (parser output), `llm-client.md` (model/provider layer). These are the
   shared contracts between lanes; treat them as API law.
3. **`V0-NOTES.md`** — append-only ruling record (Rulings 1–13; Ruling 14 is recorded in code and
   commit `9b72aba`). It explains *why* the model pins, cost accounting, and safety guards look the
   way they do. Read it before touching `src/wikimind/prices.py` or any model pin.
4. **`PROJECT.md`** — the specification / "agent constitution" (v0.2). Read-only governance. It
   holds the thesis (§1–§4), the data model (§6), the invariants (§6.4), the agent protocols (§10),
   the trust model (§12), and the glossary (§18). Where `DECISIONS.md` supersedes it, the decision
   wins.
5. **`V0_PROTOCOL.md`** — unchanged by D14. It is **not** a pre-build gate anymore; it is the
   specification for the *later full evaluation on the finished engine*.
6. **`PRE_REGISTRATION.md`** — the freeze record (tag `v0-preregistered`). Together with 4 and 5 it
   defines which files are frozen.
7. **`README.dev.md`** — the developer guide: setup, DB workflow, tests, worktree protocol.
8. **`docs/lanes/PLAN.md`** — lane ownership, Gate A / Gate B criteria, merge order.

**Frozen / read-only:** `DECISIONS.md`, `PROJECT.md`, `V0_PROTOCOL.md`, `docs/contracts/*.md`, and
`PRE_REGISTRATION.md` MUST NOT be edited from a lane branch. `V0-NOTES.md` is append-only until the
V0 decision table is applied. A required change to a frozen document needs explicit human
authorization and a new dated ruling/decision entry naming what it replaces — never a silent edit.

---

## 2. The thesis in one minute

> RAG moves all work to query time and redoes it on every question. Compendium moves the work to
> ingest time, does it once, and keeps it maintained. **Compile once; traverse and read forever.**

- A researcher drops sources (papers, articles, notes) into a **project** (one research question =
  one wiki). An agent compiles them into a persistent, versioned, provenance-tracked knowledge
  graph — the **Wiki**.
- Questions are answered by **traversing the compiled graph and reading distilled pages**, not by
  re-retrieving raw text. Every answer carries citations; valuable answers are filed back as new
  pages, so knowledge compounds.
- Formally: **Wiki = projection(raw sources, event log)** — a materialized view. Raw sources are
  immutable; every agent action is an append-only event. Therefore the Wiki is always rebuildable,
  auditable, reviewable, and removable by replay.
- **Non-goals:** not a chatbot with upload, not a search engine, not a note-taking app, not one
  global brain, no autonomous web crawling in v1, not a reference manager.

**Target user (D13):** a researcher running literature reviews or evidence synthesis in one narrow
field. Pitch: *"your synthesis stays current as the literature changes."* The differentiator is
that a change (contradiction, supersession, retraction) reaches the **derived notes**, not that we
alert on retractions (Zotero/scite already do).

---

## 3. Architecture at a glance

```text
                  ┌────────────────────────────────────────────┐
                  │            Web App (Next.js) — FUTURE     │  ← not built in Stage 1
                  │  graph view · chat/query · diff review ·   │
                  │  ingest dropzone · lint dashboard          │
                  └───────────────┬────────────────────────────┘
                                  │ REST / SSE
                  ┌───────────────┴────────────────┐
                  │        API + Agent Runtime     │  ← future: src/wikimind/serve/
                  │  (own state machine, durable   │
                  │   job queue)                   │
                  └───┬──────────┬──────────┬──────┘
                      │          │          │
               ┌──────┴───┐ ┌────┴─────┐ ┌──┴───────────────┐
               │ Postgres │ │  LLM API │ │ Object Storage   │
               │ nodes /  │ │ (Gemini, │ │ raw/ — immutable │
               │ edges /  │ │  judge)  │ │ proposals        │
               │ versions │ │          │ │ export           │
               │ events / │ │          │ │                  │
               │ jobs/FTS │ │          │ │                  │
               └──────────┘ └──────────┘ └──────────────────┘
```

**Stage 1 (what exists now):** a Python library + Typer CLI + Postgres. There is **no web UI, no
auth, and no graph view**. The product is driven through the `wikimind` command and the library API.

**Component responsibilities:**

- **Raw layer** — immutable source blobs; raw text enters prompts only through the sanctioned
  untrusted-channel wrapper (D4), never verbatim into the knowledge graph.
- **Wiki** — the graph in Postgres: nodes, edges, claim ledgers, immutable versions, events.
- **Schema** — per-project constitution (Appendix A of `PROJECT.md`) that instantiates the
  protocols for a domain.
- **Runtime** — agent + jobs table; durable, resumable, single-writer per project.
- **Index / Render** — embedding store + retrieval substrates, and deterministic node-version
  materialization (Ruling 14).
- **Future:** `serve/` (query/answer/export) and `integrity/` (lint/removal/entailment) — see §7.

**The two-level compile (D2):**

1. **Level 1** — one **reference node** per document; depends only on the document, prompt, model,
   and project schema. Memoized (not pure — LLM output varies). Carries addressable **passages**
   with content-hash **anchors** (D4).
2. **Level 2** — **entity** and **concept** nodes built from reference nodes. Each keeps a
   **claim ledger** of claim blocks pointing to Level-1 claim IDs; the readable page is a rendered
   view of the ledger and can be regenerated (D3). Equivalence checks compare ledgers, not prose.

Removing a document = delete its reference node, drop the ledger blocks citing its claims, and
re-render affected pages. **No full replay is needed** (D2). Do not use the word "replay" for LLM
re-generation — replay is deterministic and runs from stored event payloads; regeneration re-runs
the model; tombstone is an archived node retained in history (D5).

---

## 4. First principles and invariants

Load-bearing ideas — every design decision traces to these (`PROJECT.md` §4):

1. **The Wiki is a materialized view** — `Wiki = projection(raw_sources, event_log)`; always
   rebuildable, auditable, reviewable, deletable by replay.
2. **Provenance everywhere** — every edge and node records supporting sources. Provenance is data,
   not documentation; trust tiers and removal policy are computed from it.
3. **Distillation, not fragmentation** — raw text never enters the graph; nodes are authored
   knowledge units. **Never call a node a "chunk."**
4. **Trust as data** — freshness (`status`, `stale_after`), verification (`verified`), source
   credibility, and contradictions are first-class, queryable fields.
5. **Single-writer per project** — mutating jobs serialize through a per-project queue; projects
   run concurrently. Implemented as `wikimind.db.with_writer_lock(project_id)`.
6. **Files are the interchange format; the database is the operational truth** — Postgres is the
   canonical store; OKF v0.2 bundles (markdown + YAML) are the portability/export format.

**Invariants that MUST hold at all times (`PROJECT.md` §6.4, as amended by DECISIONS):**

1. Every edge MUST record at least one supporting source in `sources` (enforced in
   `edge_sources`; `db check` verifies).
2. Every node MUST have `id`, `type`, `title`, `description`, `status`, `sources` (memory nodes may
   have empty sources).
3. Every page MUST be self-contained — no unresolved pronouns or "the document"; write
   "Attention Is All You Need (arXiv:1706.03762)".
4. One node per distinct real-world entity — **now a lint target, not a hard invariant** (D6: no
   deterministic check can enforce it). Merge via `node_redirects` + lint.
5. Agents MUST NOT write to the raw layer; raw sources are immutable.
6. Node IDs are stable slugs and MUST NOT be reused after deletion.
7. Every write to a node creates a new immutable version; versions are never mutated (Ruling 14).
8. Provenance granularity: page-level + edge-level by default; claim-level (inline attribution)
   REQUIRED only for contested/high-stakes claims.

**Two graphs (`PROJECT.md` §7):** the **knowledge graph** (entity/concept/reference/synthesis nodes
+ edges; append + supersede, never deleted, only flagged) answers "what is known"; the **memory
graph** (session summaries, decisions, preferences, question history; decays/compacts) answers
"what happened / what did the user decide / what was already asked." Remember the distilled
residue, not everything. Memory never enters the knowledge graph's claim structure.

---

## 5. Vocabulary — use these terms exactly

Terms are load-bearing; do not synonymize.

| Term | Exact meaning |
| --- | --- |
| **Source (document)** | An immutable raw file (PDF, HTML, note) in object storage. Never modified; never enters the graph as text. |
| **Node (page)** | A unit of distilled knowledge with identity, frontmatter, body, and edges. Types: `reference`, `entity`, `concept`, `synthesis`, `memory`. Never call it a "chunk." |
| **Edge** | A typed, provenance-bearing relationship between two nodes. |
| **Passage** | An addressable, content-hash-anchored span of a reference node (Level 1). |
| **Claim** | A unit of assertion with an anchor quote/span; identified by a deterministic `claim_id`. |
| **Claim ledger** | A Level-2 node's ordered list of claim blocks (each points to Level-1 claim IDs + a status); the page is a rendered view of it. |
| **Provenance** | Record of which source documents support a node or edge (`node_sources`, `edge_sources`). |
| **Distillation** | Ingest-time conversion of a source into a reference node + updates to other nodes. The opposite of chunking. |
| **Wiki** | A project's entire knowledge graph (nodes + edges + versions). |
| **Materialized view** | The Wiki, formally — a projection over (raw sources + event log) that can be rebuilt. |
| **Event** | Immutable record of one agent/user action; the log is the timeline. |
| **Proposal** | Pending agent edit awaiting approval (the productized pull request); never holds the writer lock (D9). |
| **Lint** | Scheduled health pass (contradictions, orphans, staleness, duplicates, memory compaction). |
| **Tombstone** | An archived node/document — excluded from answers, retained in history. |
| **Filtered replay** | Full rebuild by re-running distillation, skipping specified documents. The only exact hard-delete semantics. |
| **Dirty-page update** | Touching only pages whose claims a new source affects. |
| **Trust tier** | Derived label (high/medium/low) from source credibility, verification, corroboration. |
| **Progressive disclosure** | Cheap projection (types + descriptions) first; full bodies only for selected nodes. |
| **Replay / Regeneration / Tombstone** | Replay = deterministic, from stored event payloads. Regeneration = re-run the LLM. Tombstone = archived node in history. Never conflate (D5). |

**Edge type enum:** `relates_to`, `part_of`, `supports`, `contradicts`, `supersedes`, `cites`,
`authored_by`, `uses`, `measured_by`. New types MUST be added to the schema contract first.

---

## 6. Decisions and rulings — quick reference

Read the source documents for exact wording; this table is a memory aid only.

| ID | Decision (essence) |
| --- | --- |
| **D1** | Compile once, per project. Two-level compile (hypothesis under test). |
| **D2** | Two-level compile: L1 reference node per document (memoized, not pure); L2 entity/concept nodes from L1; removal re-renders affected pages, no full replay. |
| **D3** | Claim ledger: L2 pages hold claim blocks pointing to L1 claim IDs; pages are renderable views; equivalence compares ledgers, not prose; claim-to-claim relations = typed edges. |
| **D4** | Passage layer: content-hash passage anchors; raw text quarantined & role-isolated, enters prompts only for drill-down/quote checks in marked untrusted context. |
| **D5** | Three distinct terms: replay (deterministic, from event payloads), regeneration (re-run LLM), tombstone (archived node kept in history). |
| **D6** | Schema fixes: provenance in join tables (`node_sources`, `edge_sources`) with FKs, not JSONB; node key `(project_id, slug)`; `node_redirects` for merges; one-node-per-entity is a lint target; `project_id` on every table from day one. |
| **D7** | Tiered entailment checks: full on L1 claims and L2 blocks touched by a new/removed source; sample the rest; leak scan after every removal. |
| **D8** | Pages are untrusted — distilled text can carry injected instructions; treat it as data in later prompts. |
| **D9** | Proposals never hold the writer lock — store edit intent, release lock, rebase on approval. |
| **D10** | Embeddings from M0 — pgvector ships at the foundation; entry-node selection uses search, not a full node list. |
| **D11** | One cost envelope per user-month: $15 revenue, 40 papers, 200 queries. Gate $0.08/paper & $0.02/query; target $0.04 & $0.01. A PIVOT also needs projected COGS ≤ $9/user-month. |
| **D12** | Accuracy first, then cost, with fixed levers: Flash for distillation, Pro for answer/entailment; dirty-page updates; append ledger rows; render prose on read; cap drill-down passages; cache L1. |
| **D13** | Target user = evidence-synthesis researcher; pitch = "your synthesis stays current as the literature changes." |
| **D14** | Engine first, measurement later. V0 pre-build gate retired; `V0_PROTOCOL.md` survives as the spec for the full evaluation; mini-check (~30 Q) after the engine answers end-to-end; Stage 1 = Python library + CLI + Postgres. |

**Rulings (governance `V0-NOTES.md`):** R1 `git init -b main` authorized; R3 M3b 80–90% repair band
inserted pre-freeze; R5/R8/R10/R12/R13 model stack evolution; R6 shadow cost at production list
prices; R7 rate limits are schedule, not scope; R9 V0 gate retired (D14); **R10/R13** judge MUST
never be a Gemini family model (anti-self-preference bias) — enforced by a guard test that MUST
NOT be deleted or weakened; **R11** `:free` routes are always priced at their paid twin, never $0;
**R12** drafter pinned to a model proven live through our own client; **R14** compile must
materialize node versions (`render/`, commit `9b72aba`). Any model-pin change requires a new ruling
naming its predecessor.

**Current pins (`src/wikimind/prices.py` — single source of truth):**

- Drafter: `gemini-3.1-flash-lite` (Gemini API).
- Judge: `nvidia/nemotron-3-ultra-550b-a55b:free` (OpenRouter), shadow-priced at paid twin
  `nvidia/nemotron-3-ultra-550b-a55b` ($0.60/M in, $2.40/M out).
- Embedder: `BAAI/bge-small-en-v1.5`, `EMBED_DIM = 384`.
- Pins were verified 2026-10-27; catalog presence is not evidence of liveness.

---

## 7. Implementation status and repository map

**Honest status:** Gate A (foundation) is done; the engine is largely built; `src/wikimind/integrity/`
landed with Lane 8 (CI #35). Gate B remains incomplete because `serve/` does not exist yet. The
`v0-fixtures` corpus is mid-compile.

### What is implemented

| Package (`src/wikimind/`) | Responsibility |
| --- | --- |
| `cli.py`, `config.py`, `prices.py` | Root Typer app (tolerant sub-app mounting); `.env` + redacted settings; model pins/prices. |
| `db/` | Connection, ordered migrations with SHA-256 verification, per-project advisory writer lock, `db check` invariant checker, `db reset`, CLI. |
| `parse/` | Strict `ParsedDocument`/`Channel` contract; deterministic text and offline pypdf parsers; optional GROBID client; untrusted-channel prompt helper; registry; CLI. |
| `llm/` | Provider protocols, metered Gemini drafter and OpenRouter judge (retries + JSON schema), JSONL cost meter, fakes, embedders, redaction, CLI smoke/costs. |
| `compile/` | Five-stage pipeline (analyze → dedup → write → judge → commit), deterministic IDs, pinned prompts, provenance-carrying commits, unit short-circuit, CLI + project/document registration. |
| `index/` | Embedding store, incremental current-version indexer, ANN/FTS/k-hop/hybrid retrieval **substrates** (no ranking/fusion policy yet), CLI. |
| `render/` | Deterministic node rendering, append-only version materialization (Ruling 14), backfill with repair, CLI. |
| `integrity/` | Lint, contradiction/staleness checks, removal workflow (tombstone / retracted / guarded duplicate), post-removal leak scan, entailment sampling, reconciliation, CLI (Lane 8, CI #35; removal from Lane 12). |

**Planned / not implemented:** `src/wikimind/serve/` (query traversal, page reading, answer
synthesis, export/import) exists only in `docs/lanes/PLAN.md`. `node_redirects` exists in the schema
but has no application write path yet. `llm/cost.py::DbCostSink` is an explicit `NotImplementedError`
stub — the cost ledger today is `costs.jsonl` on disk, not the `cost_events` table. The `integrity/`
package is **implemented**, not planned: it landed with Lane 8 (CI #35), writes `jobs`,
`ledger_blocks`, and `events`, and now hosts the end-to-end removal workflow.

**Lane plan (`docs/lanes/PLAN.md`)** — note the actual merge history renumbered the area lanes:
`lane/0-trunk` (deps, DB, CI) → `lane/1-parse` → `lane/2-models` → `lane/3-compile` → `lane/4-index`
→ `lane/5-versions` (the `render/` package) → `lane/6-hygiene`. The plan document still describes the
earlier L4=serve / L5=integrity split; treat the code and git history as authoritative for what has
landed, and the plan for what remains. Lanes own their files: stay inside your lane, read anything,
write only your lane's files, never edit another lane's `pyproject.toml`.

### Other top-level content

- `migrations/` — `000_enable_vector.sql`, `001_schema_v1.sql`, `002_channels_per_document.sql`,
  `003_compile_runs.sql`, `004_compile_units.sql`. Migrations keep LF endings on Windows.
- `tests/` — 33 test files (~330 tests) covering DB/config/migrations/locks/checks, parse contract and
  golden fixtures, LLM secrets/cost/retry/judge/fakes, compile pipeline, index, render. DB tests skip
  cleanly when `WIKIMIND_DB` is unset or unreachable; tests never need keys or network.
- `docs/contracts/` — frozen `schema.md`, `parsed-document.md`, `llm-client.md`.
- `docs/lanes/PLAN.md`, `docs/runbooks/morning-run.md`, `docs/v0/interview-script.md`.
- `data/` — morning-run logs, archived cost ledgers, and the ~68 MB Retraction Watch CSV plus a
  Semantic Scholar probe (V0 corpus material, legal sources only).
- `costs.jsonl` — append-only shadow-cost ledger (~1,384 events, ≈ $1.50 shadow, $0 actual).
- `.github/workflows/ci.yml` — ruff, pytest (with pgvector service), pytest-no-db.
- `V0-NOTES.md`, `V0_PROTOCOL.md`, `PRE_REGISTRATION.md`, `PROJECT.md`, `DECISIONS.md` — governance.

---

## 8. Data model / schema (Postgres)

`docs/contracts/schema.md` is the frozen contract; read it before any schema change. Conventions:
Postgres 14+, pgvector `vector(384)`, UUID PKs (`gen_random_uuid()`), `project_id UUID NOT NULL`
on every project-scoped table, CHECK-constraint enums, SHA-256 content addresses, and FKs — not
JSONB — for provenance and cross-table integrity. `DECISIONS.md` wins on conflict.

| Table | Key columns / notes |
| --- | --- |
| `projects` | `id`, `name`, `created_at`. One research question = one wiki. |
| `schema_migrations` | Runner-created: `name` PK, `sha256`, `applied_at`. Verified on re-run. |
| `jobs` | `id`, `kind`, `state` (`queued/running/done/failed/crashed`), `payload JSONB`, `heartbeat_at`. Durable, resumable. |
| `documents` | `id`, `sha256`, `source_uri`, `parser`, `prompt_version`, `model`, `schema_version`. `UNIQUE(project_id, sha256, parser, prompt_version, model, schema_version)` = the L1 memo key. |
| `channels` | `id TEXT`, PK `(id, document_id)`, `kind`, `ordinal`, `raw_text` (the only place raw source text lives). |
| `nodes` | `id`, `slug`, `node_type` (`citation/entity/concept/synthetic/memory`), `status` (`active/stale/superseded/archived/retracted`), `current_version_id`, `UNIQUE(project_id, slug)`. |
| `node_versions` | `id`, `node_id`, `version > 0`, `rendered_text`, `fts TSVECTOR` generated + GIN index, `UNIQUE(node_id, version)`. **Append-only.** |
| `node_redirects` | PK `(project_id, from_slug)`, `node_id` — handles entity merges. |
| `claims` | `id`, `citation_node_id`, `channel_id`, `document_id`, `anchor_quote`, `anchor_span JSONB`, `text`, `created_by_job`; composite FK `(channel_id, document_id)` → `channels`. |
| `ledger_blocks` | `node_id`, `claim_id NULL`, `position`, `block_type` (`claim/derived`), CHECK tying `claim_id` to type, `UNIQUE(node_id, claim_id)`. |
| `edges` | `src_node`, `dst_node`, `edge_type` (9-value enum in §5), `UNIQUE(project_id, src_node, dst_node, edge_type)`. |
| `edge_sources` | PK `(edge_id, claim_id)` — claim-level edge provenance (mandatory; verified by `db check`). |
| `node_sources` | PK `(node_id, document_id)` — page-level provenance. |
| `embeddings` | `node_version_id`, `model`, `vec vector(384)`, `UNIQUE(node_version_id, model)`, HNSW cosine index. |
| `cost_events` | `job_id`, `call_site`, `model`, `in_tokens`, `out_tokens`, `unit_price`, `shadow_cost_usd`, `meta JSONB`. Schema exists; app does not write it yet. |
| `events` | `id BIGSERIAL`, `kind`, `data JSONB`, `created_at` — append-only audit log. |
| `compile_runs` | `started_at`, `finished_at`, `status`, `stats JSONB` — one row per `wikimind compile`. |
| `compile_units` | PK `(document_id, channel_id, compile_version)`, `run_id`, `compiled_at` — unit short-circuit marker. |

**Why this shape (do not relitigate without a ruling):** no MongoDB — edges/provenance need FK
integrity and atomic 10–15-node ingests; no Neo4j yet — traversal is shallow, recursive CTEs
suffice; no separate vector DB — pgVector covers search; the bottleneck is the LLM, not the DB; git
is a feature (`export → OKF bundle → optional push`), not infrastructure.

**Canonical invariants checked by `wikimind db check`:** every expected table exists and has
`project_id NOT NULL`; migration hashes match; pgvector present; no orphan rows; every edge has
provenance; claim/document provenance consistent; enum domains valid; embeddings are 384-dim.
Exit codes: 0 clean, 1 warnings, 2 errors.

**Writer lock:** take `with wikimind.db.with_writer_lock(project_id)` before any mutation. It is a
transaction-scoped `pg_advisory_xact_lock(hashtextextended(key::text, 0))` on a canonical
UUID/string key; `writer_lock_held(conn, project_id)` verifies via `pg_locks` (used by `render`).
Reads are always allowed. Projects mutate in parallel; within one project, writes serialize.

---

## 9. Agent protocols (from `PROJECT.md` §10, normative)

These are the behaviors the system is being built to implement. An agent operating a live instance follows them; a coding agent implements them.

**Ingest (one source):** enqueue a job serialized per project → fetch raw → parse (GROBID for academic PDFs, HTML to markdown, plain text) → **distill** with a Flash-tier model → identify-or-mint entities/concepts (FTS + embedding + schema disambiguation; flag rather than guess a merge) → mint the 1:1 reference node → write provenance-bearing edges (contradictions get `contradicts` edges, never a silent winner) → **dirty-page discipline** (only touch pages this source affects) → emit events → land edits as proposals in review mode, else auto-merge → report an ingest summary such as "touched 12 pages, 2 contradictions flagged".

**Query (one question):** resolve anaphora using memory → read the lightweight projection → select entry nodes by search (not a full node list) → traverse edges 1–2 hops → read full bodies only of selected nodes → **sufficiency check: never fabricate to close a gap** → synthesize with citations (node IDs + source documents) → quote-level verification (M2) → file durable answers back as `synthesis` nodes → remember a session summary. Trust tier and status MUST surface on every cited claim.

**Lint:** unresolved `contradicts` edges · orphans with no inbound edges (except references) · concepts mentioned on 3 or more pages but lacking their own node · nodes past `stale_after` · near-duplicate entity nodes (merge candidates) · edges/nodes whose sources reference archived/retracted documents (mark claims unsupported) · memory past compaction threshold. Lint produces **findings and proposals, not silent fixes**.

**Removal policy:** deletion is treated as re-derivation. Ingest is non-commutative, so affected pages must be re-rendered. The default action is a tombstone (`status: archived`), which keeps history but excludes the node from answers. A source known to be wrong gets `status: retracted` and lint flags claims supported only by it. A duplicate ingest removes its reference node and edges only after confirming no other page cites them. Legal hard-delete uses a filtered replay (full rebuild skipping that source).

**Memory compaction:** sessions older than a threshold are summarized into residue; residue is re-summarized as it ages. Emits `memory.compacted`. Never compact knowledge nodes.

**Review workflow:** in review mode, agent edits are proposals (diffs against the current version). Approval applies the diff and bumps the version — the productized pull request. Proposals never hold the writer lock (D9); store the edit intent, release the lock, rebase on approval.

**Concurrency:** single-writer per project via the job queue and advisory lock; reads always allowed; projects run in parallel; jobs are durable/resumable from the last emitted event. Job states: `pending → running → (awaiting_review) → done | failed`.

**Trust lifecycle:** node status moves `active → stale` (past `stale_after`, reversible via re-verification), `superseded` (newer claim), `archived` (tombstone), or `retracted` (source invalid, dependents flagged). Trust tier is derived from source credibility + verification + corroboration count, and MUST be surfaced on every cited claim. Answer-time verification quote-checks each claim against its cited source before presenting the answer.

---

## 10. Development workflow

**Prerequisites:** Python 3.11.x specifically (3.12+ changes Neumaier summation and shifts FakeEmbedder pinned digests by 1 ULP); local PostgreSQL 18 on `localhost:5432` with pgvector; role `wikimind` (non-superuser, `CREATEDB`); template database `wikimind_template` (owner `wikimind`, `vector` installed, `IS_TEMPLATE`) so databases can be created `WITH TEMPLATE` since the role cannot run `CREATE EXTENSION`. On Windows invoke the interpreter explicitly (`.\.venv\Scripts\python.exe`); shell activation is not assumed. No Docker locally; CI uses a `pgvector/pgvector:pg16` service container.

**Setup (Windows):**
```powershell
git fetch origin
git worktree add ..\WikiMind-L0 -b lane/0-trunk origin/main
cd ..\WikiMind-L0
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

**.env (gitignored, worktree root):**
```dotenv
WIKIMIND_DB=postgresql://USER:PASSWORD@localhost:5432/wikimind_l0
GEMINI_API_KEY=
OPENROUTER_API_KEY=
WIKIMIND_GROBID_URL=
```
The config loader reads `WIKIMIND_DB`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `WIKIMIND_GROBID_URL`; real environment variables win over `.env`. `wikimind.config.redact()` and `wikimind.llm.secrets.redact()` are available for diagnostics. Other env controls: `WIKIMIND_ENV_FILE`, `WIKIMIND_COSTS_PATH`, `WIKIMIND_LIVE=1` (required for live smoke/compile), `WIKIMIND_COMPILE_DELAY`.

**Database workflow:**
```powershell
.\.venv\Scripts\wikimind.exe db migrate
.\.venv\Scripts\wikimind.exe db check
```
`db migrate` applies `migrations/NNN_name.sql` in filename order; each file and its `schema_migrations` row commit atomically; a changed file exits 2. `db check` exits 0/1/2 (clean/warnings/errors). `db reset --confirm` is destructive and rebuilds from the template. Use `--migrations-dir PATH` for wheel installs or relocated checkouts.

**Verify before handing work back:**
```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
```
DB tests skip cleanly when `WIKIMIND_DB` is unset or unreachable; tests never need API keys or network. `WIKIMIND_DB` must name a role with `CREATEDB`.

### CLI commands (root `wikimind`)

```text
wikimind db migrate [--dsn] [--migrations-dir]
wikimind db check   [--dsn] [--migrations-dir]
wikimind db reset   --confirm [--dsn] [--migrations-dir]
wikimind parse run <file> [--json] [--parser NAME]
wikimind parse grobid-check
wikimind llm smoke [--costs-path PATH]        # dry-run unless WIKIMIND_LIVE=1 with both keys
wikimind llm costs [--path PATH]
wikimind project create <name> [--dsn]
wikimind project list [--dsn]
wikimind document add <project> <files...> [--dsn] [--parser NAME]
wikimind document list <project> [--dsn]
wikimind compile <project> [--dsn] [--costs-path PATH] [--delay N] [--min-score 0..5]
wikimind index <project> [--dsn] [--batch-size N] [--costs-path PATH]
wikimind render backfill <project> [--dsn]
```
Standalone module entry points also exist: `python -m wikimind.parse.cli`, `.index.cli`, `.render.cli`, `.llm.cli`. `compile` requires `WIKIMIND_LIVE=1`.

### Worktree protocol

| Lane | Worktree | Branch | Owns |
| --- | --- | --- | --- |
| L0 trunk | `..\WikiMind-L0` | `lane/0-trunk` | pyproject, schema, DB, CI, dev docs |
| L1 parse | `..\WikiMind-L1` | `lane/1-parse` | `src/wikimind/parse/` |
| L2 models | `..\WikiMind-L2` | `lane/2-models` | `src/wikimind/llm/` |
| L3 compile | `..\WikiMind-L3` | `lane/3-compile` | `src/wikimind/compile/` |
| L4 index | `..\WikiMind-L4` | `lane/4-index` | `src/wikimind/index/` |
| L5 versions | `..\WikiMind-L5` | `lane/5-versions` | `src/wikimind/render/` |
| L6 hygiene | `..\WikiMind-L6` | `lane/6-hygiene` | cross-lane cleanup |
| L7 qa | — (merged) | `lane/7-qa` | `src/wikimind/qa/` — answer path and `ask` CLI |
| L8 integrity | — (merged) | `lane/8-integrity` | `src/wikimind/integrity/` — checks, ledger, events, jobs |
| L9 remediation | — (merged, fast-forward) | `lane/9-remediation` | QA recall/ranking and golden set (`src/wikimind/qa/`, `tests/golden/`) |
| L10 standing docs | — (merged) | `lane/10-standing-docs-sync` | `AGENTS.md`, `V0-NOTES.md`, QA pipeline sync |

Rules for every lane: stay inside your owned files (read anything, write only your lane's files); never edit another lane's `pyproject.toml` (L0 declares all dependencies); use your own database (`wikimind_l0` for L0; test databases are per-session); make small present-tense commits (e.g. `db: add migration runner`); the agent may commit and push lane branches, but **never force-push** or rewrite published history (Ruling 28(1)/(3)); merges into main and pushes of main are human actions (Ruling 28(1)); if blocked for about 30 minutes, commit WIP, report `BLOCKED`, and stop.

**Merge ritual (human):** merge `lane/0-trunk` first from an up-to-date `main`, then the area lanes (their `<area>/cli.py` sub-apps mount automatically through the static import list in `src/wikimind/cli.py`). After each merge: reinstall, `db migrate`, `db check`, pytest, ruff. Resolve conflicts in favour of the schema contract and decision log. CI runs ruff and pytest against a pgvector service with `WIKIMIND_DB` set; embedder tests use `pytest.importorskip` and torch is absent in CI.

**Common troubleshooting:** the `vector` extension permission issue is solved by creating databases from `wikimind_template`; a hash mismatch is an error, not a prompt to edit `schema_migrations`; on Windows keep LF endings in migrations (`git config core.autocrlf false` or a `.gitattributes` rule); tests skipped means `WIKIMIND_DB` is unset/unreachable or lacks `CREATEDB`; an `ImportError` at CLI startup is expected for a lane that has not merged yet.

---

## 11. Hard rules — do not violate these

**Governance & scope**
- Never edit frozen documents from a lane branch (`DECISIONS.md`, `PROJECT.md`, `V0_PROTOCOL.md`, `PRE_REGISTRATION.md`, `docs/contracts/`). `V0-NOTES.md` is append-only. A change needs explicit human authorization and a new dated entry naming what it replaces.
- Stay inside your lane's files. Do not touch another lane's code or `pyproject.toml`. The agent may commit and push lane branches (Ruling 28(1)); never force-push or rewrite published history (Ruling 28(3)). Merges into main and pushes of main are human actions only.
- If asked to build a web UI, auth, or graph view, note that these are out of Stage 1 scope and require a human decision.

**Terminology**
- Never call a node a "chunk." Use the vocabulary in §5 exactly. Do not say "replay" when you mean regeneration.

**Security & secrets**
- Never commit `.env`, print a DSN, or paste credentials into a report or log. Keys are env-only. Use `redact()` / `redact_secret()` in diagnostics. Rotate any exposed key.
- Raw document text is **untrusted**. It MUST NOT be inserted verbatim into prompts; use `parse.channels_for_prompt` with its `<untrusted source="channel:...">` wrappers, XML escaping, and character budgets. Distilled pages are also untrusted (D8) — injected instructions can survive summarization.
- Injections from documents MUST NOT drive tool calls or system instructions. Keep extraction prompts isolated from source content via structured extraction.
- Multi-tenancy: every project-scoped table carries `project_id`; all queries and writes MUST be scoped by it.

**Data integrity**
- Take `with_writer_lock(project_id)` before any mutation. Every query/search filter by `project_id`.
- Every edge MUST have provenance in `edge_sources`; every node MUST have a supporting source (except memory nodes). Do not bypass these invariants.
- Append-only stores (`events`, `node_versions`, `costs.jsonl`) are never mutated in place. Every node write creates a new version.
- Determinism is a contract: content-hash IDs use NFC + whitespace normalization and the `\x1f` separator; golden SHA-256 tests and `PYTHONHASHSEED`-independent IDs protect it. Keep migrations LF on Windows.

**Models & cost**
- Model pins live only in `src/wikimind/prices.py`. The judge MUST NOT be a Gemini-family model; the guard test in `tests/test_llm_cost.py` MUST NOT be deleted or weakened. `:free` routes are always shadow-priced at their paid twin, never $0.
- Every LLM call goes through the meter, including $0 calls. `compile` and `llm smoke` require `WIKIMIND_LIVE=1` for real calls.
- Pin changes require a new ruling naming the predecessor and proving liveness through our own client (`wikimind llm smoke` with `WIKIMIND_LIVE=1`); a catalog listing is not evidence of liveness.

**Quality bar**
- Tests never require API keys or network; fake clients cover all model paths. Run ruff check, ruff format --check, and pytest before handing work back. Keep new code within the frozen contracts in `docs/contracts/`.

---

## 12. Operational state (as of 2026-11-24)

- Branch `main` tracks `origin/main` (Lane 12 dispatch verified it at `a1ed2e9`); the tree is clean except untracked `.vscode/` and `data/`.
- The `v0-fixtures` corpus compile is **complete: 229/229 units**. Verified read-only against `wikimind_live_l3`: documents 4, claims 530, edges 1593, compile_units 229, cost_events 1427. `costs.jsonl` holds **1,434 events** (~$1.51 shadow cost, $0 actual), dominated by `compile.write` / `compile.judge` / `compile.analyze`. Daily OpenRouter `:free` caps produced HTTP 429 stops and Gemini 503 retries recorded in `data/morning-run-*.log`; the morning-run trigger is retired.
- Full evaluation under `V0_PROTOCOL.md` is still ahead (D14). A ~30-question directional mini-check runs once the engine answers end-to-end; CI thresholds apply only to the full run.
- Corpus material in `data/v0/` must stay legal sources only (Semantic Scholar, arXiv, Retraction Watch).

## 13. Reading paths — pick by task

| Your task | Read |
| --- | --- |
| Any design or scope question | `DECISIONS.md` → `docs/contracts/` → `PROJECT.md` |
| Change the DB schema | `docs/contracts/schema.md` → `migrations/` → `src/wikimind/db/` → DECISIONS D6 |
| Work on parsers or prompt wrapping | `docs/contracts/parsed-document.md` → `src/wikimind/parse/` |
| Change model clients, pins, cost, retries | `V0-NOTES.md` rulings → `src/wikimind/prices.py` → `docs/contracts/llm-client.md` → `src/wikimind/llm/` |
| Work on ingestion/distillation | DECISIONS D1–D4, D7 → `src/wikimind/compile/` → `src/wikimind/render/` |
| Work on retrieval/QA | D10, `PROJECT.md` §10.2 → `src/wikimind/index/` (note: `serve/` not yet built) |
| Work on lint/removal | D5–D9, `PROJECT.md` §10.3–10.4 → `src/wikimind/integrity/` (landed with Lane 8, CI #35) |
| Set up or run verification | `README.dev.md` → `pyproject.toml` → `.github/workflows/ci.yml` |

## 14. First-session checklist

1. Read §0–§5 of this file. Confirm you are working on the Python engine, not the legacy Next.js prototype.
2. Read `DECISIONS.md` and the contract for the area you will touch.
3. Identify your lane and its write scope; do not edit outside it.
4. Set up the venv and confirm `wikimind db migrate` and `wikimind db check` pass on your database.
5. Run `ruff check .`, `ruff format --check .`, and `pytest` before making changes to establish a green baseline.
6. Make a small, present-tense commit. If blocked ~30 minutes, commit WIP, report `BLOCKED`, stop.
7. Before handing back, re-run the full verification and report what changed, what was tested, and any decision/ruling implications.

---

**Maintainers:** keep this file current when lanes land or decisions change. It is agent-facing
onboarding, not governance — for authoritative rules always defer to `DECISIONS.md` and the frozen
contracts in `docs/contracts/`.

