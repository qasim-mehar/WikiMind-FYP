# WikiMind developer guide

WikiMind (product concept: Compendium) compiles source documents into a
persistent, versioned, source-tracked knowledge graph in Postgres. Stage 1 is
a Python library, a Typer CLI, and Postgres. There is no web UI, auth, or graph
view in this stage.

`DECISIONS.md` is the governing decision log. `PROJECT.md` and
`V0_PROTOCOL.md` are read-only governance; `docs/contracts/schema.md` is the
database contract. Do not edit any of those files from a lane branch.

## Prerequisites

- Python 3.11 or newer.
- Postgres 14 or newer with pgvector available. `000_enable_vector.sql` runs
  `CREATE EXTENSION IF NOT EXISTS vector`.
- On Windows, invoke the interpreter explicitly; shell activation is not
  required and should not be assumed by scripts.

If the local server has no pgvector, use the pinned Docker fallback:

```powershell
docker run -d --name wikimind-pg -p 5433:5432 -e POSTGRES_PASSWORD=wikimind pgvector/pgvector:pg16
```

Then point `WIKIMIND_DB` at `localhost:5433`.

## Setup

```powershell
git fetch origin
git worktree add ..\WikiMind-L0 -b lane/0-trunk origin/main
cd ..\WikiMind-L0
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Create the target database if it does not exist, then create the gitignored
`.env` in the worktree root:

```dotenv
WIKIMIND_DB=postgresql://USER:PASSWORD@localhost:5432/wikimind_l0
GEMINI_API_KEY=
OPENROUTER_API_KEY=
WIKIMIND_GROBID_URL=
```

Never commit `.env`, print a DSN, or paste credentials into a report. The
config loader reads `WIKIMIND_DB`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, and
`WIKIMIND_GROBID_URL` from the environment; existing environment variables win
over `.env`. `wikimind.config.redact()` is available for diagnostic messages.

## Database workflow

```powershell
.\.venv\Scripts\wikimind.exe db migrate
.\.venv\Scripts\wikimind.exe db check
```

- `db migrate` applies `migrations/NNN_name.sql` in filename order. Each file
  and its `schema_migrations` row commit atomically. Re-running verifies every
  recorded SHA-256; a changed file exits 2.
- `db check` exits 0 when clean, 1 for warnings, and 2 for errors. It verifies
  `project_id NOT NULL`, migration hashes, orphan rows, edge provenance, enum
  domains, and 384-dimensional embeddings.
- `db reset --confirm` drops and recreates the `public` schema and re-applies
  migrations. It is intentionally destructive and requires the flag.

Always scope queries and writes by `project_id`. Take the single-writer lock
with `wikimind.db.with_writer_lock(project_id)` before mutating a project.

## Tests

```powershell
$env:WIKIMIND_DB = "postgresql://USER:PASSWORD@localhost:5432/wikimind_l0"
.\.venv\Scripts\python.exe -m pytest
```

`tests/conftest.py` creates `wikimind_test_<pid>` (plus a suffix for
per-test databases), optionally applies migrations, and drops the databases on
teardown. If `WIKIMIND_DB` is unset or unreachable, DB tests are skipped, so
the suite is green without a database. Tests never require API keys or network
access. `WIKIMIND_DB` must name a role with `CREATEDB`.

Run lint and tests before handing a lane back:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
```

## Worktree protocol

| Lane | Worktree | Branch | Owns |
| --- | --- | --- | --- |
| L0 trunk | `..\WikiMind-L0` | `lane/0-trunk` | pyproject, schema, DB, CI, dev docs |
| L1 parse | `..\WikiMind-L1` | `lane/1-parse` | `src/wikimind/parse/` |
| L2 models | `..\WikiMind-L2` | `lane/2-models` | `src/wikimind/llm/` |

Rules for every lane:

- Stay inside your owned files. Read anything, write only your lane's files.
- Do not edit another lane's files, especially `pyproject.toml`; L0 declares
  all dependencies for the project.
- Use your own database (`wikimind_l0` for L0; test databases are per-session).
  Never point a test at another lane's database.
- Make small present-tense commits, for example `db: add migration runner`.
- Never force-push. Do not push lane branches; the human merges locally.
- If blocked for about 30 minutes, commit WIP, report `BLOCKED`, and stop.

## Merge ritual (human)

1. Start from an up-to-date `main` and merge `lane/0-trunk` first. This lands
   the single `pyproject.toml`, migrations, and root CLI/scaffolding.
2. Merge the area lanes (`lane/1-parse`, `lane/2-models`, then later lanes).
   Their `<area>/cli.py` sub-apps mount automatically through the static
   import list in `src/wikimind/cli.py`.
3. From the merged worktree, reinstall and verify:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
   .\.venv\Scripts\wikimind.exe db migrate
   .\.venv\Scripts\wikimind.exe db check
   .\.venv\Scripts\python.exe -m pytest
   .\.venv\Scripts\python.exe -m ruff check .
   ```

4. Resolve conflicts in favour of the schema contract and decision log. Run
   the checks again after every resolution. Do not amend or force-push a shared
   branch.
5. CI runs ruff and pytest against a `pgvector/pgvector:pg16` service with
   `WIKIMIND_DB` set. Embedder tests use `pytest.importorskip`, and torch is not
   installed in CI.

## Lane map and gates

`docs/lanes/PLAN.md` contains the condensed lane table and the two internal
build gates. Gate A is the foundation gate after L0-L2; Gate B is the engine
gate after L3-L5. Neither gate replaces the later evaluation in
`V0_PROTOCOL.md` (D14).

## Troubleshooting

- **`vector` extension unavailable**: install pgvector for your server or use
  the Docker fallback, then re-run `db migrate`.
- **`db check` says a migration is not applied**: run `db migrate`. A hash
  mismatch is an error, not a prompt to edit `schema_migrations`.
- **Tests skipped**: `WIKIMIND_DB` is unset or unreachable, or the role lacks
  `CREATEDB`.
- **ImportError during root CLI startup**: this is expected for an area whose
  lane has not merged yet; the remaining areas still mount.
