# Lane plan (condensed)

The build is split into six lanes. Each lane has its own worktree and branch;
the human merges locally. `DECISIONS.md` remains authoritative, and
`docs/contracts/schema.md` is the shared database contract.

| Lane | Branch | Scope | Writes | Exit condition |
| --- | --- | --- | --- | --- |
| L0 trunk | `lane/0-trunk` | Dependencies, src layout, config, DB module, migrations, DB invariants, CI, dev docs | `pyproject.toml`, `.gitignore`, `.github/`, `migrations/`, `docs/contracts/schema.md`, `docs/lanes/PLAN.md`, `README.dev.md`, `src/wikimind/{__init__,cli,config}.py`, `src/wikimind/db/**`, `tests/conftest.py`, `tests/test_db_*.py` | Fresh DB migrates; `db check` exits 0; tests and ruff green |
| L1 parse | `lane/1-parse` | Source detection, PDF/HTML/text parsing, channel construction, untrusted tagged text helper, `parse` CLI | `src/wikimind/parse/**`, its tests, allowed docs | Parsed channels preserve order/text; raw text only through the channel layer; CLI mounts |
| L2 models | `lane/2-models` | Provider abstraction, metered LLM client, embedding interface, prompt/response schemas | `src/wikimind/llm/**`, its tests, allowed docs | Fake-client tests pass without keys/network; no unmetered call path |
| L3 compile | `lane/3-compile` | L1 citation extraction, L2 claim ledgers, entity/concept pages, jobs, writer lock use, ingest/removal pipeline | `src/wikimind/compile/**`, its tests, allowed docs | End-to-end ingest of fixtures produces citation/entity/concept pages with claim provenance; no full replay for removal |
| L4 serve | `lane/4-serve` | Query traversal, page reading, answer synthesis, export/import surface (CLI/library only in Stage 1) | `src/wikimind/serve/**`, its tests, allowed docs | Cited answers traverse the graph, not RAG retrieval; file-back path works |
| L5 integrity | `lane/5-integrity` | Lint, contradiction/staleness checks, removal leak scan, entailment sampling, reconciliation jobs | `src/wikimind/integrity/**`, its tests, allowed docs | Lint/removal checks produce findings/proposals without silent fixes; leak scan clean on fixtures |

## Build gates

These are internal build-readiness gates for Stage 1, not the later V0
evaluation gates. V0 remains the specification for the post-engine evaluation
(D14).

**Gate A - foundation (after L0, L1, L2).**

- All lane tests run without API keys or network.
- Fresh DB: `wikimind db migrate` exits 0; `wikimind db check` exits 0.
- Schema contract is present; every table and required unique constraint
  exists; every project-scoped query uses `project_id`.
- Parser output and model interfaces are stable enough for L3 to consume.
- `wikimind` mounts all merged area sub-apps; ruff is clean.

**Gate B - engine (after L3, L4, L5).**

- A source can be ingested into citation and L2 pages, queried through graph
  traversal, and removed with affected pages re-rendered and no leaks.
- Claims cite channel passages; edges and pages carry provenance.
- Writer lock serializes mutations per project; jobs are crash-resumable.
- Full pytest suite, `db check`, and CI are green.
- A directional mini-check can run end-to-end on the finished engine; the full
  V0 protocol is scheduled after Gate B.

## Merge order

Merge L0 first because it lands the only `pyproject.toml` and the schema.
Then merge area lanes. Root CLI sub-app mounting is tolerant of absent areas,
so intermediate merges remain runnable. After each merge: reinstall, migrate,
check, test, lint. No lane pushes or force-pushes.
