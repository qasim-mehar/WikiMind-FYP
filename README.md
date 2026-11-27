# WikiMind / Compendium

WikiMind compiles source documents into a persistent, versioned,
provenance-tracked knowledge graph — the **Wiki**. Questions are answered by
traversing that compiled graph and reading distilled pages, with citations.

**Stage 1 is a Python 3.11 library + Typer CLI + PostgreSQL 18 with pgvector.**
There is no web UI, no auth, and no graph view yet. The product is driven
through the `wikimind` command and the library API. Thesis: *compile once;
traverse and read forever*.

## Quickstart

Prerequisites: CPython **3.11.x** specifically (3.12+ shifts pinned golden
digests), PostgreSQL 18 with pgvector, role `wikimind` with `CREATEDB`, and a
`wikimind_template` database carrying the `vector` extension. On Windows,
invoke the interpreter explicitly.

```powershell
git clone https://github.com/qasim-mehar/WikiMind-FYP.git
cd WikiMind-FYP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Create a gitignored `.env` in the checkout root (never commit it or print the
DSN):

```dotenv
WIKIMIND_DB=postgresql://USER:PASSWORD@localhost:5432/wikimind_l0
GEMINI_API_KEY=
OPENROUTER_API_KEY=
```

Then migrate, verify, and test:

```powershell
.\.venv\Scripts\wikimind.exe db migrate
.\.venv\Scripts\wikimind.exe db check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pytest
```

Database tests skip cleanly when `WIKIMIND_DB` is unset or unreachable; no API
keys or network are needed. `db check` exits 0 clean / 1 warnings / 2 errors.
Never run `db reset --confirm` against the live corpus database.

Common commands: `wikimind project create`, `wikimind document add`,
`wikimind compile <project>` (requires `WIKIMIND_LIVE=1`), `wikimind index
<project>`, `wikimind render backfill <project>`, `wikimind ask "<question>"`,
and the `wikimind integrity` group (`check`, `contradictions`, `verify-ledger`,
`remove`, `leaks`).

For a single-step ingest, use `wikimind add <project> <files...>` (also requires
`WIKIMIND_LIVE=1`). It shows extract / estimate / compile stages, prints a
conservative shadow-cost preview before any LLM call, and asks for confirmation;
pass `--yes` for non-interactive runs. Re-adding an already-compiled file is a
no-op — deterministic claim IDs and unit markers prevent duplicate work or
claims. A compile pass brings the whole project up to date (other pending
files are included; already-compiled units are skipped). Pass `--force` to
clear those markers and re-run distillation (regeneration, not replay; dedup
still suppresses duplicate claims). Use
`--no-compile` to extract and register only, and `--create-project` to create a
missing project. The low-level `document add` + `compile` pair remains available
for scripted control.

## Documentation ladder

1. **`AGENTS.md`** — start here: orientation, invariants, vocabulary, hard rules.
2. **`README.dev.md`** — setup, DB workflow, tests, worktree protocol.
3. **`DECISIONS.md`** — governing decision log (D1–D14); read before design questions.
4. **`docs/contracts/`** — frozen database, parsed-document, and LLM-client contracts.

Also authoritative: `PROJECT.md` (specification), `V0-NOTES.md` (append-only
rulings), and `docs/lanes/PLAN.md` (lane ownership and merge order).
`DECISIONS.md`, `PROJECT.md`, `V0_PROTOCOL.md`, `PRE_REGISTRATION.md`, and
`docs/contracts/` are read-only from lane branches.

## Legacy prototype

An earlier "WikiMind AI" web-app README (Next.js, MongoDB, chat) is preserved
in git history. That prototype is not present in this checkout; when it
disagrees with the engineering docs above, the engineering docs win.
