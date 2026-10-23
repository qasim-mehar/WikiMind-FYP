# PROJECT.md — Compendium (working title)

> **SUPERSEDED BY DECISIONS.md — READ THAT FIRST.** DECISIONS.md supersedes this document for every item listed there. DECISIONS.md wins on disagreement. V0_PROTOCOL.md is unchanged and is the specification for the later full evaluation on the finished engine (D14); it is not a pre-build gate. v0.2 records the D14 build-order reversal.

**Status**: v0.2 (2026-10-24) — Specification & Agent Constitution. Pre-implementation (M0 not started).
**Audience**: Human engineers, LLM coding agents (Codex, Claude Code, etc.), and LLM agents that will operate the running system.
**Evolution**: This document is versioned in the decision log (§17). Changes require a new entry there. Nothing in this file is sacred except the invariants (§6.4) — challenge anything else with a reasoned proposal.

## 0. How to read this document

Normative language. The key words MUST, MUST NOT, SHOULD, and MAY are to be interpreted as in RFC 2119. Protocol steps in §10 are normative.

Vocabulary is load-bearing. Terms like node, edge, source, distillation have exact meanings (§18 Glossary). Use them consistently in code, DB schemas, prompts, and generated content. Do not synonymize (e.g., never call a node a "chunk").

Read paths by role:

| You are… | Read |
| --- | --- |
| An engineer/coding agent building the system | Everything, especially §4–§14 |
| An agent operating a live Compendium instance | §10 (protocols), §12 (trust), Appendix A (SCHEMA.md) |
| A reviewer / new contributor | §1–§5, §15, §16 |

## 1. Executive summary

Compendium is a web-based, multi-tenant research tool. A researcher drops sources (papers, articles, notes) into a project. An LLM agent compiles them into a persistent, versioned, provenance-tracked knowledge graph — the Wiki — consisting of interlinked distilled pages (entities, concepts, claims, syntheses). Questions are answered by traversing the compiled graph and reading pages, not by re-retrieving raw text. Every answer carries citations; valuable answers are filed back into the Wiki as new pages, so knowledge compounds.

The thesis, in one line: RAG moves all work to query time and redoes it on every question. Compendium moves the work to ingest time, does it once, and keeps it maintained. Compile once; traverse and read forever.

Non-goals (do not build these):

- Not a chatbot with file upload. Not a search engine. Not a note-taking app.
- Not one global brain: the Wiki is scoped per project (one research question, one Wiki).
- No autonomous web crawling in v1 (user-curated sources only; web ingest of user-supplied URLs is allowed and must pass §13 sanitization).
- Not a Zotero/Paperplug replacement (reference management); we consume documents, we don't manage bibliographies.

## 2. The problem: why RAG fails

Standard RAG (NotebookLM-style: upload files → chunk → embed → retrieve top-k → generate) has four architectural flaw families:

- **One-shot, query-blind retrieval.** Multi-hop questions die (a single embedding query cannot surface hop-2 documents). Fixed top-k regardless of question difficulty. Embedding similarity finds text about a topic, not text containing the answer.
- **Lossy context handling.** Chunking cuts tables and paragraphs mid-thought; entity ambiguity ("Apple" company vs. fruit); irrelevant chunks dilute attention and actively increase hallucination.
- **Single blind reasoning pass.** No loop that checks "is the evidence sufficient?"; contradictory evidence is silently blended; no aggregation across chunks.
- **Pipeline-level blindness.** Conversational follow-ups decay; indexes go stale with no freshness signal; citations are unverified; corpus content can inject instructions into the generator; evaluation cannot separate retrieval failure from generation failure.

Diagnosis: these flaws are architectural, not fixable by patches (re-ranking, HyDE, query rewriting) which address symptoms. The fix requires inverting where the work happens — which is the entire premise of this project.

Flaw → mechanism mapping (compressed):

| RAG flaw | Compendium mechanism |
| --- | --- |
| Multi-hop dies | Hops are pre-materialized as page links; query = graph traversal |
| Fixed top-k | Agentic loop with sufficiency check (§10.2) |
| Similarity ≠ evidence | Pages are curated distillations, not raw fragments |
| Chunking damage | No chunks; whole self-contained pages; one page per entity |
| Noise dilution | Few targeted, pre-distilled pages enter context |
| No reflection | Agent iterates; checks status/verified before citing |
| Contradictions blended | Flagged at ingest; OKF trust signals make them first-class |
| Conversational decay | Persistent Wiki + memory nodes across sessions |
| Stale knowledge | stale_after / status + lint pass |
| Unverified citations | Provenance chain: answer → node → sources → raw doc |
| Broken evals | The Wiki itself is testable: versions, link integrity, lint |

Honest residual gaps: live numeric aggregation across many pages, and multi-hop paths never materialized, still require agent reasoning at query time. We do not claim these are solved.

## 3. Intellectual lineage

| Ancestor | What it is | What we took |
| --- | --- | --- |
| LLM Wiki pattern | Idea-file: an agent incrementally builds and maintains a personal wiki between the user and raw sources; operations = ingest / query / lint; the wiki is a persistent, compounding artifact | The process model (§10), the three layers (raw/wiki/schema), lint-as-garbage-collection, answers-filed-back |
| OKF v0.2 (GoogleCloudPlatform/open-knowledge-format) | Vendor-neutral spec: knowledge as markdown + YAML frontmatter; provenance, trust, freshness as queryable fields; graph-shaped cross-links; progressive disclosure; git-native bundles | The export target: an OKF v0.2 bundle from the pinned repository; node frontmatter schema, sources/verified/status/stale_after, trust tiers, and the Cytoscape viewer as UI seed |
| Memex (Vannevar Bush, 1945) | Curated personal knowledge store with associative trails; maintenance burden unsolved | The vision. The LLM solves the maintenance problem — it doesn't get bored updating cross-references |

Our export target is OKF v0.2, pinned to `GoogleCloudPlatform/open-knowledge-format`; isolate all format code behind one module.

## 4. First principles

These are the load-bearing ideas. Every design decision downstream traces to one of these.

1. **The Wiki is a materialized view.** Formally: Wiki = projection(raw_sources, event_log). Raw sources are immutable; every agent action is recorded as an append-only event. Therefore the Wiki is always rebuildable, auditable, reviewable, and deletable by replay (§10.4). This single principle is what makes trust, versioning, and removal coherent.
2. **Provenance everywhere.** Every edge and node records which source documents support it. Provenance is data, not documentation — it is what the removal policy and trust tiers are computed from.
3. **Distillation, not fragmentation.** Raw text never enters the knowledge graph. Nodes are authored knowledge units with identity, title, description, body, and links. (Terminology rule: we do not use the word "chunk" for anything in this system.)
4. **Trust as data.** Freshness (status, stale_after), verification (verified), source credibility, and contradictions are first-class fields, queryable at answer time.
5. **Single-writer per project.** Within a project, agent jobs serialize through a queue. Projects run concurrently. This gives conflict-free writes without merge machinery.
6. **Files are the interchange format; the database is the operational truth.** Postgres is the canonical store for a running multi-tenant deployment. OKF bundle directories (markdown + frontmatter) are the export/import/portability format — demoting git from infrastructure to a feature ("export to repo").

## 5. System overview

```text
                 ┌────────────────────────────────────────────┐
                 │                Web App (Next.js)           │
                 │  graph view · chat/query · diff review ·   │
                 │  ingest dropzone · lint dashboard          │
                 └───────────────┬────────────────────────────┘
                                 │ REST / SSE
                 ┌───────────────┴────────────────┐
                 │        API + Agent Runtime     │
                 │  (Python/FastAPI, own state    │
                 │   machine, durable job queue)  │
                 └───┬──────────┬──────────┬──────┘
                     │          │          │
              ┌──────┴───┐ ┌────┴─────┐ ┌──┴───────────────┐
              │ Postgres │ │ LLM API  │ │ Object Storage   │
              │  nodes / │ │ (Gemini, │ │  raw/ — immutable│
              │  edges / │ │  tiered) │ │                  │
              │ versions │ └──────────┘ │  proposals       │
              │ events / │              │  export          │
              │ jobs     │              └──────────────────┘
              │ FTS      │
              │ (+pgvec) │
              └──────┬───┘
                     │ export/import job
              ┌──────┴─────────────────────┐
              │  OKF bundle (directory of  │
              │  .md + YAML) → git sync,   │
              │  viz.html, external tools  │
              └────────────────────────────┘
```

Components: Raw layer (immutable sources in object storage), Wiki (the graph in Postgres), Schema (per-project constitution — see Appendix A), Runtime (agent + jobs), Web app, Export layer (OKF).

## 6. Data model

### 6.1 Nodes (each = one graph vertex, one wiki page)

| Type | What it is | Multi-source? | Lifecycle / deletion semantics |
| --- | --- | --- | --- |
| reference | Distillation of exactly one source document (the "what this paper says" page) | No — 1:1 with a document | Soft-dies with its document (§10.4) |
| entity | A real-world thing: person, org, dataset, model, method. One node per distinct real-world entity — disambiguation by construction | Yes | Survives document removal; sources shrinks |
| concept | Topic, technique, theory | Yes | Same as entity |
| synthesis | Filed-back answers, comparisons, mini-surveys — derived, regenerable | Derived | Regenerable; may be archived |
| memory | Session summaries, user decisions, preferences — the episodic layer | — | Compacts over time (§7) |

### 6.2 Frontmatter (OKF v0.2 core, per node)

```yaml
---
id: entity/transformer-architecture      # stable
slug: <type>/<kebab-name>
type: entity
title: Transformer architecture
description: >                           # ≤160 chars; doubles as the index entry
  Sequence model architecture based entirely on attention, dispensing
  with recurrence and convolution.
tags: [deep-learning, attention, architecture]
sources:                                 # provenance: which reference nodes ground this page
  - ref: references/arxiv-1706-03762
    credibility: high
generated: 2026-06-12                    # when the agent wrote/last revised it
verified: 2026-06-14                     # when a human or verification pass confirmed it
status: active                           # active | stale | superseded | archived | retracted
stale_after: 2027-06-12                  # review-by date (optional)
---
# Markdown body — self-contained, no dangling references
```

### 6.3 Edges (Compendium extension; each carries mandatory provenance)

OKF v0.2 core links are directed and untyped. Their relationship kind belongs in the surrounding prose, not in the link. Compendium's typed edges are a local extension, not an OKF feature.

```yaml
from: concept/self-attention
to: entity/transformer-architecture
type: part_of                            # see enum below
sources: [references/arxiv-1706-03762]   # documents supporting THIS edge
```

Compendium edge type enum (extensible, but new types MUST be added to SCHEMA.md first): relates_to, part_of, supports, contradicts, supersedes, cites, authored_by, uses, measured_by.

On export, each typed edge is written as a plain directed OKF markdown link, with its relationship type stated in the surrounding prose, and also recorded in the Compendium-specific `compendium_edges` frontmatter field. External OKF tools are not expected to read `compendium_edges`.

### 6.4 Invariants (normative — these MUST hold at all times)

1. Every edge MUST record at least one supporting source in sources.
2. Every node MUST have: id, type, title, description, status, sources (memory nodes may have empty sources).
3. Every page MUST be self-contained: no unresolved pronouns or references to "the document" — write "Attention Is All You Need (arXiv:1706.03762)".
4. One node per distinct real-world entity. Never two nodes for the same entity; if found, merge via lint.
5. Agents MUST NOT write to the raw layer. Raw sources are immutable.
6. Node IDs are stable slugs and MUST NOT be reused after deletion.
7. Every write to a node creates a new immutable version; versions are never mutated.
8. Provenance granularity: page-level + edge-level by default; claim-level (inline source attribution on individual bullet claims) is REQUIRED only for contested or high-stakes claims.

## 7. The two graphs

| | Knowledge graph | Memory graph |
| --- | --- | --- |
| Contains | entity, concept, reference, synthesis nodes + edges | memory nodes: session summaries, decisions, user preferences, question history |
| Lifecycle | Append + supersede. Never deleted, only flagged (stale, superseded, archived, retracted) | Decays: older sessions summarized into residue; residue re-summarized |
| Purpose | Answers "what is known" | Answers "what happened, what did the user decide, what was already asked" |
| Failure mode if uncured | Contradiction swamp | Noise dilution — recreating RAG's context-stuffing problem one level up |

Design rule: remember the distilled residue of everything, not everything. Memory compaction runs during lint (§10.5). Memory nodes feed query-time personalization and conversational continuity, never enter the knowledge graph's claim structure.

## 8. Storage architecture

Postgres is the single operational store. Object storage holds immutable blobs.

```sql
-- Core sketch (trimmed; see DDL module for full definitions)
CREATE TABLE projects (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  schema_md TEXT NOT NULL,              -- per-project constitution (Appendix A), versioned via events
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE documents (                -- raw sources (metadata only; blob lives in object storage)
  id UUID PRIMARY KEY,
  project_id UUID REFERENCES projects(id),
  object_key TEXT NOT NULL,             -- immutable raw file location
  title TEXT, source_url TEXT, doi TEXT,
  status TEXT NOT NULL DEFAULT 'active',  -- active | archived | retracted | purged
  ingested_at TIMESTAMPTZ,
  metadata JSONB
);

CREATE TABLE nodes (
  id TEXT PRIMARY KEY,                  -- stable slug, e.g. entity/transformer-architecture
  project_id UUID REFERENCES projects(id),
  type TEXT NOT NULL,                   -- reference | entity | concept | synthesis | memory
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  body_md TEXT NOT NULL,
  frontmatter JSONB NOT NULL,           -- full OKF frontmatter incl. sources & status
  current_version INT NOT NULL DEFAULT 1,
  search TSVECTOR                       -- full-text search
);

CREATE TABLE edges (
  id BIGSERIAL PRIMARY KEY,
  project_id UUID REFERENCES projects(id),
  from_node TEXT REFERENCES nodes(id),
  to_node TEXT REFERENCES nodes(id),
  type TEXT NOT NULL,
  sources JSONB NOT NULL,               -- INVARIANT 1: mandatory provenance
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE node_versions (            -- immutable history (what git used to give us)
  node_id TEXT REFERENCES nodes(id),
  version INT NOT NULL,
  body_md TEXT NOT NULL,
  frontmatter JSONB NOT NULL,
  author TEXT NOT NULL,                 -- "agent:gemini-2.5-pro" | "user:<uuid>"
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (node_id, version)
);

CREATE TABLE events (                   -- append-only; the log; powers replay & audit
  id BIGSERIAL PRIMARY KEY,
  project_id UUID,
  type TEXT NOT NULL,
  payload JSONB NOT NULL,
  actor TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE proposals (                -- pending agent edits = the "pull request"
  id UUID PRIMARY KEY,
  project_id UUID,
  node_id TEXT,                         -- + proposed version payload / edge ops
  diff JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
  created_by TEXT, reviewed_by UUID, reviewed_at TIMESTAMPTZ
);

CREATE TABLE jobs (                     -- durable, resumable agent work
  id UUID PRIMARY KEY,
  project_id UUID,                      -- queue serializes per project (§11)
  type TEXT NOT NULL,                   -- ingest | lint | replay | export | compaction
  payload JSONB NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending' -- pending | running | awaiting_review | done | failed
);
```

Why this shape (so no agent relitigates it):

- No MongoDB: edges and provenance are cross-document referential integrity; ingest atomically writes 10–15 nodes; NoSQL trades away exactly these guarantees.
- No Neo4j (yet): our traversal is shallow (index → drill → 1–2 hops → read). Recursive CTEs suffice. Flip trigger: deep pattern queries or >10M edges (or adopt Apache AGE and keep one DB).
- No separate vector DB: search is a derived feature; pgvector covers M4.
- The bottleneck is the LLM, not the DB. Writes are agent-paced (seconds/page); scale-out storage buys nothing we can use.
- git is a feature, not infrastructure: "export project → OKF bundle directory → optional push to user's git repo." Gives portability and review-friendly artifacts without multi-tenant filesystem ops.
- Progressive disclosure (in-DB): queries start from a lightweight projection — node list filtered by type with description one-liners — then load full bodies of selected nodes only. The index.md file is regenerated on OKF export.

## 9. Event taxonomy

Append-only. Every agent action MUST emit one.

| Event | Emitted when |
| --- | --- |
| project.created / schema.updated | project scaffold / constitution edited |
| document.ingest.started / .completed / .failed | ingest job lifecycle |
| node.created / node.updated | new page / new version (payload: node_id, version) |
| edge.added / edge.removed | graph structure change (payload includes sources) |
| proposal.created / .approved / .rejected | review workflow |
| document.archived / .retracted / .purged | removal lifecycle (§10.4) |
| answer.filed | a query answer became a synthesis node |
| lint.completed | lint pass done (payload: findings) |
| memory.compacted | memory summarization ran |
| replay.completed | rebuild finished (payload: filter used) |

## 10. Agent protocols (normative)

The agent is a rule-follower, not a free spirit: its behavior is fully specified by the project's SCHEMA.md (Appendix A), which instantiates these protocols.

### 10.1 Ingest (one source document)

1. Job enqueued; runtime serializes per project (§11).
2. Fetch raw from object storage. Parse: GROBID for academic PDFs (sections, tables, citations); HTML→markdown for pages; plain text passthrough.
3. Distill: extract key claims, mentioned entities/concepts, methods, findings, and the document's relation to existing knowledge. This is LLM work — tier: Flash-class.
4. Identify or mint: for each entity/concept, match against existing nodes (FTS + embedding similarity + SCHEMA.md disambiguation rules). Mint new node or plan an update (new version) to the existing one. When in doubt, flag for human review rather than guessing a merge.
5. Mint the reference node (1:1 with this document).
6. Write edges with provenance. If new claims conflict with existing nodes: add contradicts edges, annotate both pages, do NOT silently pick a winner.
7. Dirty-page discipline: only update pages whose claims this source actually affects. Never re-touch the whole neighborhood.
8. Emit events; if review mode is on, changes land as proposals; else auto-merge. Record an ingest summary for the user ("touched 12 pages, 2 contradictions flagged").

### 10.2 Query (one question)

1. Resolve: if conversational, rewrite to a standalone question using memory/session context (anaphora resolution).
2. Plan: read the lightweight projection (type-filtered node list + descriptions). Select entry nodes.
3. Traverse: follow edges 1–2 hops; select pages to read.
4. Read: load full bodies of selected nodes only.
5. Sufficiency check: does the evidence answer the question? If not: traverse further, or state what's missing, or suggest new sources to ingest. NEVER fabricate to close a gap.
6. Synthesize with citations. Every claim cites node id(s) + source document(s).
7. Verify (M2): quote-level check that cited sources actually support each claim.
8. File back: if the answer has durable value (comparison, analysis, discovered connection), create a synthesis node — explorations compound like ingested sources.
9. Remember: write a session summary to a memory node.

### 10.3 Lint (scheduled or user-triggered health pass)

Checklist: unresolved contradicts edges · orphan nodes with no inbound edges (except references) · concepts mentioned on ≥3 pages but lacking their own node · nodes past stale_after · near-duplicate entity nodes (merge candidates) · edges/nodes whose sources reference archived/retracted documents → mark claims unsupported · memory nodes past compaction threshold. Lint produces findings and proposals, not silent fixes.

### 10.4 Removal (the policy that motivated the architecture)

Principle: deletion is a re-derivation problem, not a removal problem. Ingest order is non-commutative: later documents were distilled against a graph that contained the removed one. You cannot un-bake an ingredient; you can only re-bake without it. The event log makes operational rebuild exact, but semantic removal of an entangled document requires provenance flags or re-ingestion. Therefore:

| Reason for removal | Action | Effect |
| --- | --- | --- |
| Done with it / out of scope | status: archived (tombstone) | Excluded from traversal & answers; retained in history. Reversible. Default action. |
| Source retracted / known-wrong | status: retracted | Reference node flagged; lint marks claims/edges solely supported by it unsupported; agent re-derives affected sections |
| Duplicate / bad ingest | Delete reference node + its edges | Only after verifying nothing cites it in sources |
| Legal / privacy hard delete | Filtered replay | Full rebuild: re-run distillation of all remaining documents from raw. Expensive, exact, rare |

### 10.5 Memory compaction

Sessions older than threshold are summarized into residue; residue is re-summarized as it ages. Compaction emits memory.compacted. Never compact knowledge nodes.

### 10.6 Review workflow

In review mode, agent edits are proposals (diffs against current version). Approval applies the diff and bumps the version. This is the productized pull request.

## 11. Concurrency model

- Single-writer per project: the job queue serializes all mutating jobs within a project. Read queries always allowed (they see committed versions).
- Projects are independent: parallelism across projects is unbounded (within worker capacity).
- Jobs are durable and resumable: state lives in the jobs table; a crashed ingest resumes from the last emitted event.
- Job states: pending → running → (awaiting_review) → done | failed.

## 12. Trust, verification, and status lifecycle

Node status state machine:

```text
        stale_after passed
 active ─────────────────────►  stale ──► (re-verified → active)
   │
   ├─ superseded by newer claim ──►  superseded
   ├──────────────────────────────►  archived   (tombstone; reversible)
   └──────────────────────────────►  retracted  (source invalid; dependents flagged)
```

Trust tier (derived, not vibes): computed from sources credibility + verified + corroboration count. Example tiers: high = verified + ≥2 independent sources; medium = single credible source, unverified; low = single low-credibility source or contested claims. The query protocol MUST surface tier and status on every cited claim.

Answer-time verification (M2): the synthesizing pass must quote-check each claim against the cited source text before presenting the answer.

## 13. Security

- Injection surface: raw documents are quarantined (never enter prompts verbatim); agents read distillations and structured parse output. Web-sourced ingestion still passes raw text through the distiller — treat parsed content as untrusted input: no instruction-following from document bodies, strip/neutralize embedded directives, and keep the distillation prompt isolated from source content via structured extraction.
- Multi-tenancy: all tables carry project_id; enforce isolation with row-level security or scoped query middleware. Node IDs are namespaced per project.
- Immutable audit: events + node_versions are append-only; no UPDATE/DELETE grants for the agent role.

## 14. Technology stack

| Layer | Choice | Rationale / notes |
| --- | --- | --- |
| Canonical store | Postgres (managed: Neon / Supabase / Cloud SQL) | §8. FTS now; pgvector at M4 |
| Blob store | S3 / GCS | raw/ immutable; exports |
| Backend | Python + FastAPI | Matches OKF reference-agent ecosystem; GROBID, LLM SDKs |
| PDF parsing | GROBID (+ fallbacks) | Purpose-built for academic PDFs. Validate on our corpus in week 1 |
| LLM | Gemini — Flash-class for ingest/distill, Pro-class for synthesis/query — behind a thin provider abstraction | OKF-native path; swappable |
| Frontend | Next.js (React) | Graph view: fork OKF's Cytoscape viz.html as seed; marked for markdown |
| Agent runtime | Own state machine + jobs table | No framework lock-in; resumable |
| Interchange | OKF v0.2 bundle export/import | Portability is a feature (anti-lock-in selling point) |

## 15. Milestones

| Scope | Exit demo |
| --- | --- |
| M0 — Ingest works: Repo scaffold, SCHEMA.md v0, GROBID pipeline, ingest protocol end-to-end: one arXiv paper → reference + entity/concept nodes + provenance-bearing edges → events + version + log | Drop a paper, watch the graph light up |
| M1 — Query works: Lightweight projection, graph viewer, query protocol (§10.2 steps 1–6), answer with citations, file-back as synthesis | Ask a multi-hop question; cited answer; answer becomes a node. MVP = M0 + M1 |
| M2 — Trust works: Lint v1 (all §10.3 checks), removal flows (§10.4), filtered replay tool, proposal review UI, answer-time quote verification | Retract a paper; watch dependents flag; graph stays truthful |
| M3 — Memory works: Session memory nodes, cross-session recall, compaction | Return next day; follow-up question resolves context from memory |
| M4 — Scale: Batch ingest, cost dashboards, hybrid BM25+vector search behind same interface, OKF import/export + git sync | Export project → open viz.html anywhere |

## 16. Risks

| Risk | Mitigation |
| --- | --- |
| PDF parse quality caps everything downstream | Validate GROBID on target corpus in week 1; fallback parsers; ingest QA checks |
| Ingest cost grows with graph connectivity | Dirty-page discipline (§10.1.7); Flash-tier models for distillation |
| LLM edge/consistency drift | Deterministic validators (frontmatter schema, link integrity, invariant checks §6.4) + semantic lint |
| Injection via web-sourced docs | §13 quarantine + structured extraction |
| OKF v0.2 is young; spec will drift | Pin version; single format module; watch canonical repo |
| Event log ≠ semantic deletion | Honest docs (§10.4); tombstone-first UX |

## 17. Open decisions (decision log)

| # | Question | Status |
| --- | --- | --- |
| D1 | Cloud: GCP (Gemini+OKF-native) vs. provider-neutral (Neon/Supabase) | Open |
| D2 | Trust posture default: auto-merge vs. PR-gated per project | Open (lean: per-project setting, default auto-merge with spot-check UI) |
| D3 | Product name ("Compendium" is a placeholder) | Open |
| D4 | Auth: email/OAuth/passkeys | Open |
| D5 | Free-tier cost ceiling per user (ingest is LLM-expensive) | Open |

## 18. Glossary (normative vocabulary)

1. **Source (document):** an immutable raw file (PDF, HTML, note) in object storage. Never modified, never enters the knowledge graph as text.
2. **Node (page):** a unit of distilled knowledge with identity, frontmatter, body, and graph edges. Types: reference, entity, concept, synthesis, memory. Never call a node a "chunk."
3. **Edge:** a typed, provenance-bearing relationship between two nodes.
4. **Provenance:** the record of which source documents support a node or edge.
5. **Distillation:** the ingest-time act of converting a source into reference-node content and updates to other nodes. The opposite of chunking.
6. **Wiki:** the entire knowledge graph of a project (nodes + edges + versions).
7. **Materialized view:** the Wiki, formally — a projection over (raw sources + event log) that can always be rebuilt.
8. **Event:** an immutable record of one agent/user action. The log is the timeline.
9. **Proposal:** a pending agent edit awaiting approval (the productized pull request).
10. **Lint:** the scheduled health pass over the Wiki (contradictions, orphans, staleness, duplicates, memory compaction).
11. **Tombstone:** an archived node/document — excluded from answers, retained in history.
12. **Filtered replay:** full rebuild of the Wiki by re-running distillation, skipping specified documents. The only exact hard-deletion semantics.
13. **Dirty-page update:** touching only pages whose claims a new source affects.
14. **Trust tier:** derived label (high/medium/low) computed from source credibility, verification, and corroboration.
15. **Progressive disclosure:** navigation pattern — cheap projection (types + descriptions) first, full bodies only for selected nodes.

## Appendix A — SCHEMA.md v0 (per-project constitution seed)

Each project carries a SCHEMA.md row: the single source of truth that tells the operating LLM what to do. It instantiates the protocols of §10 for the project's domain. Seed structure:

```markdown
# SCHEMA.md — <project name>

## 1. Ontology        # node types in use, edge types allowed, project-specific types

## 2. Naming rules    # slug conventions; disambiguation rules; when to mint vs. extend

## 3. Ingest protocol # §10.1 instantiated: what to extract from THIS domain's sources

## 4. Query protocol  # §10.2 instantiated: answer formats, citation style

## 5. Lint protocol   # §10.3 thresholds (orphan rules, dedup similarity cutoffs)

## 6. Removal policy  # §10.4, verbatim reference + project specifics

## 7. Memory protocol # what to remember, compaction thresholds

## 8. Review policy   # auto-merge or gated; who approves
```

SCHEMA.md changes are themselves events (schema.updated) — the constitution has history.

## Changelog

- v0.1 — Initial specification. Consolidates planning sessions: RAG flaw analysis, LLM Wiki–OKF synthesis, data model, removal policy, Postgres-core storage for public multi-tenant deployment, milestones M0–M4.
- v0.2 (2026-10-24): D14 recorded. Build order reversed — engine first, measurement after. Stage 1: Python library, CLI, Postgres; no UI, no auth, no graph view.
