# Compendium DECISIONS

This record lists every decision that survived review, one entry each, so humans and coding agents share one source of truth.

Oct 16, 2026 · @Qasim Ali

## Status

Thirteen decisions are frozen. Nothing is built yet. V0 (see the V0 PROTOCOL doc) tests them before any platform work starts.

- The invariants from PROJECT.md section 6.4 still apply, except invariant 4, which is now a lint target (D6).
- A decision changes only through a new entry here that names the entry it replaces.
- O2 done: OKF v0.2 verified, typed edges are a local extension.
- Open items: O1 choose the domain. O3 pick the cloud (PROJECT.md section 17, item D1).

## Architecture decisions

1. **D1. Compile once, per project.** The system reads each source once and keeps a wiki for one research question. This is still a hypothesis. V0 tests it against strong RAG before we commit to it.
2. **D2. Two-level compile.** Level 1 is one reference node per document. It depends only on the document, the prompt, the model, and the project schema. Level 2 is entity and concept nodes built from reference nodes. Removing a document means deleting its reference node, dropping the ledger blocks that cite its claims, and re-rendering the affected pages. No full replay is needed. Level 1 is memoized, not pure, because LLM output varies.
3. **D3. Claim ledger.** Each level 2 node keeps a ledger: a list of claim blocks. Each block points to level 1 claim IDs and holds a status. The readable page is a rendered view of the ledger and can be regenerated. Equivalence checks compare ledgers, not prose. Relations between claims (for example, "A contradicts B") are typed edges between claim IDs. Anything that does not reduce to claim IDs becomes a derived block. We flag it and entailment-check it. V0 measures what share of level 2 content is derived.
4. **D4. Passage layer.** Reference nodes carry addressable passages. Claim IDs cite passage IDs built from content hashes, not ordinal numbers. Raw text is quarantined and role-isolated, not absent. It enters a prompt only for drill-down and quote checks, and always in a marked, untrusted context.
5. **D5. Three terms, not one.** Replay is deterministic and runs from stored event payloads. Regeneration re-runs the LLM. Tombstone means an archived node that stays in history. Never use "replay" for regeneration.
6. **D6. Schema fixes.** Provenance lives in join tables (node\_sources, edge\_sources) with foreign keys, not JSONB. The node key is (project\_id, slug). A node\_redirects table handles merges. One node per real-world entity is a lint target, not a hard invariant, because no deterministic check can enforce it. Every table keeps project\_id from day one, even while auth and row-level security wait.

## Trust and safety decisions

1. **D7. Tiered entailment checks.** An entailment check asks whether the cited passage supports the claim. Run it in full on every level 1 claim. Run it in full on level 2 blocks that a new or removed source touches. Sample it on all other blocks. Run a leak scan after every removal. The leak scan searches the rendered prose for claims from the removed document.
2. **D8. Pages are untrusted.** Distilled pages come from untrusted text, so later prompts treat them as untrusted data. Injected instructions can survive summarization.
3. **D9. Proposals never hold the writer lock.** Jobs in review mode store the edit intent and release the project lock. Approval rebases the edit on the current version. This removes the deadlock where one waiting review blocks the whole project.
4. **D10. Embeddings from M0.** Entity matching at ingest needs similarity search, so pgvector ships in M0. Entry-node selection for queries uses search, not a full node list. A full list stops fitting in context at a few thousand nodes.

## Cost and accuracy decisions

1. **D11. One cost envelope.** Every cost threshold comes from one user-month: $15 revenue, 40 papers, 200 queries. Per-paper cost alone is the wrong unit, because the costs that grow are removal, repair, and query-time drill-down. The V0 gate allows $0.08 per paper and $0.02 per query. That is $7.20 per user-month, about 48% of revenue. The design target is $0.04 per paper and $0.01 per query. That is $3.60, about 24%. Hosting and payment fees come on top. The gate is a go or no-go for V0, not the business margin. A PIVOT also requires projected COGS of $9 or less per user-month (60% of revenue), because a per-paper cap alone would let a PIVOT lose money. If a PROCEED lands between target and gate, apply the D12 levers before M0 starts, so the 48% gate never becomes the shipped default.
2. **D12. Accuracy first, then cost, with fixed levers.** A cheaper system that loses on accuracy has no product, so the accuracy and integrity gates come before the cost gates. These levers cut cost without lowering accuracy. V0 measures each one.
   - Use a Flash-class model for distilling and updating. Use a Pro-class model only for the final answer and for entailment checks.
   - Update only the pages a new source affects.
   - Append ledger rows instead of rewriting pages.
   - Render prose only when a page is read, not at ingest.
   - Cap the passages loaded in one drill-down.
   - Cache memoized level 1 output.

## Product decisions

1. **D13. Target user and pitch.** The first user is a researcher who runs literature reviews or evidence synthesis in one narrow field. The pitch is "your synthesis stays current as the literature changes." It is not "we flag retractions." Tools like Zotero and scite already alert on retractions. Our difference is that a change reaches the derived notes. Retraction is the rare extreme of a common class: contradiction and supersession happen far more often and use the same machinery.

Two risks stay open. Retractions are rare, so the headline feature fires seldom for one user. Systematic reviewers often buy through institutions, so a $15 individual price may not fit. Five interviews run beside V0 test both risks. Ask about past behavior, such as what the researcher did the last time a cited paper was retracted or contradicted. Do not ask for opinions about this product.

## D14 — Build order reversed: engine first, measurement after

Status: Adopted. Authorized by: Qasim, 2026-10-24.

Amends:
- D1 — replaces the test-before-build order. V0 no longer gates the build.
- D13 — replaces the pitch wording only. The target user in D13 stands.

Decision:
1. Build the core engine first. Measure later, on the finished engine.
2. The pre-build steps are skipped: domain selection, corpus collection,
   manual parser tests. scripts/corpus.py is kept for the later
   evaluation (legal sources only: Semantic Scholar, arXiv,
   Retraction Watch).
3. V0 as a pre-build gate is retired. V0_PROTOCOL.md survives unchanged
   as the specification for the later full evaluation on the finished
   engine.
4. A mini-check (~30 questions) against a strong hybrid-search baseline
   runs once the engine answers end-to-end. It uses the protocol's
   metrics. It is explicitly a directional smoke test; the decision
   table's CI thresholds apply only to the full run.
5. Stage 1 scope: a Python library, a CLI, and Postgres. No web UI, no
   auth, no graph view. project_id on every table from day one.

Positioning: LLM wikis exist (Karpathy's pattern, nashsu/llm_wiki,
nvk/llm-wiki). Compendium does not claim to be first. Its claims are:
verified passage-level citations, the claim ledger, surgical removal
that rewrites only what depended on the removed source, and a
multi-user product later.

Lessons folded in: nashsu/llm_wiki (GPL-3.0 — ideas only, no code),
the LLM Wiki v2 essay and its critique thread, nvk/llm-wiki (MIT).
