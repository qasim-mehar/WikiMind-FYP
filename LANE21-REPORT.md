# LANE 21 — DEPLOYMENT PACKAGING (local/demo Docker)

## 1. Base, branch, and guards

- Base verified: `main` and `origin/main` both at
  `d362783d0c3cd8c825492866bcf87b7311e5cd63`; `git cat-file -t` → `commit`;
  `git merge-base --is-ancestor base main` → 0. **Base guard PASS (not moved).**
- Branch `lane/21-deploy` created from that base (`git switch -c lane/21-deploy main`);
  `git rev-parse HEAD` at creation = the base SHA above.
- Repository confirmed as `github.com/qasim-mehar/WikiMind-FYP`; the Lane 17
  webapp is present in the base tree (`src/wikimind/webapp/`).
- No merge, no push to `main`, no force-push, no live-corpus connection.

## 2. Scope compliance

- New files only. Staged path list contains no existing-file modification:
  `.env.example`, `Dockerfile`, `compose.yaml`, `docker/README.md`,
  `docker/entrypoint.sh`, `docker/verification.txt` (plus this report and the
  manifest, both required by the card).
- `src/` untouched — no entrypoint or source change was needed. The only
  bootstrap code added is `docker/entrypoint.sh`, a new support file that calls
  the existing CLI (`wikimind db migrate`, `wikimind project list/create`) and
  then execs `python -m wikimind.webapp`.
- Forbidden paths have an **empty diff**: `src/wikimind/llm/cost.py`,
  `migrations/`, `src/wikimind/qa/`, `src/wikimind/compile/`. No conflict with
  Lanes 19/20 was found; none was resolved by this lane.
- `.gitignore` was **not** edited. Because `.gitignore:5` has `.env.*`, the
  required `.env.example` was added with `git add -f`; `.env` itself remains
  ignored (`git check-ignore -v .env` → `.gitignore:4:.env`).
- Untracked `.vscode/`, `data/`, and an unrelated `lane19-evidence/` directory
  present in the workspace were left untouched and are not staged here.

## 3. File inventory

| File | Purpose |
| --- | --- |
| `Dockerfile` | `python:3.11-slim-bookworm`; installs the project from `pyproject.toml` (`llm` extra) plus explicit `fastapi`/`uvicorn`; non-root `wikimind` (uid/gid 10001); metadata-first layer caching; `ENTRYPOINT` bootstrap. |
| `compose.yaml` | `db` (`pgvector/pgvector:pg16`, no published ports, healthcheck) + `webapp` (built image, loopback-only publish, healthcheck, depends_on healthy DB, two named volumes). |
| `.env.example` | Placeholders only: sandbox DSNs, demo Postgres credentials, webapp identity/paths/embedder, provider keys, live flag. `WIKIMIND_COST_DB_MIRROR` deliberately absent. |
| `docker/entrypoint.sh` | Refuses protected DSN and cost mirror; applies migrations with `--migrations-dir /app/migrations`; creates the sandbox project if missing; execs the webapp as non-root. |
| `docker/README.md` | Quickstart, full env-var table, loud live-corpus warning, documented smoke test, teardown/backup/restore, implementation notes. |
| `docker/verification.txt` | Captured ruff / pytest / YAML validation / placeholder-scan outputs. |
| `SHA256SUMS.txt` | CRLF evidence manifest (Lane 13 procedure, Gate C assertion), §7. |
| `LANE21-REPORT.md` | This report. |

## 4. Security posture checklist

| Requirement | Status | Evidence |
| --- | --- | --- |
| Bind `127.0.0.1` only | **CONFIRMED** (with documented Docker nuance) | `compose.yaml` publishes exactly `127.0.0.1:8000:8000` (host publish). Structured assert passed. |
| No ports published beyond localhost | **CONFIRMED** | `db` has no `ports` key at all (asserted); the only publish is the loopback webapp mapping. |
| Secrets via environment variables only | **CONFIRMED** | Both services use `env_file: .env`; no secret is baked into the image, Dockerfile, compose, or report. |
| `.env.example` contains placeholders only | **CONFIRMED** | Keys are `REPLACE_WITH_YOUR_*`; password is `CHANGE_ME_DEMO_PASSWORD`; scan for `AIza` / `sk-…` / `ghp_` found **0** matches. |
| Real `.env` never committed; stays gitignored | **CONFIRMED** | `git check-ignore -v .env` → `.gitignore:4:.env`; `.env` is not staged. |
| Stack must not point at `wikimind_live_l3` by default | **CONFIRMED** | Default DSNs target `wikimind_demo`; `src/wikimind/webapp/config.py:FORBIDDEN_DB_NAMES` refuses the corpus and `docker/entrypoint.sh` adds a case-guard; live-corpus opt-in is documented as a loud warning in `docker/README.md` §3. |
| `WIKIMIND_COST_DB_MIRROR` never set by default | **CONFIRMED** | Absent from `.env.example` and compose; entrypoint exits 2 if it is non-empty; documented in the env table and warning. |

**Docker bind nuance (explicit):** Docker's port publisher forwards to the
container's network interface, not its loopback, so the in-container bind must
be `0.0.0.0` with `WIKIMIND_WEB_PUBLIC=1` (the app's guard waiver). Both are set
only in Compose `environment`. This does **not** expose the app: the sole host
publish is `127.0.0.1:8000`, and the database publishes nothing. This is the
standard loopback-only Compose pattern and is documented in `docker/README.md`
§1–§2. No source change was required.

## 5. Validation outputs

Docker is not installed in the packaging environment (`docker : The term
'docker' is not recognized …`), so the card's substitute was used: PyYAML
6.0.3 `yaml.safe_load` plus structural assertions. Raw outputs are in
`docker/verification.txt`.

```
=== docker --version ===
docker : The term 'docker' is not recognized as the name of a cmdlet, function, script file, or operable program.

=== yaml.safe_load + structural asserts ===
compose.yaml: YAML valid; 2 services; db unpublished; webapp published 127.0.0.1:8000:8000 only; healthchecks + volumes present

=== python -m ruff check . ===
All checks passed!

=== python -m ruff format --check . ===
202 files already formatted

=== python -m pytest  (no WIKIMIND_DB in process environment) ===
371 passed, 165 skipped in 191.36s (0:03:11)

=== python -m pytest  (WIKIMIND_DB from local .env, value never printed) ===
536 passed in 330.72s (0:05:30)
```

Baseline parity: DB **536 passed**, no-DB **371 passed + 165 skipped** —
unchanged by this lane. Ruff check and format check clean.

**LLM spend: $0.00 — no LLM calls of any kind were made.** The local venv was
missing `fastapi`/`uvicorn` (imported by `tests/test_webapp.py` but undeclared
in `pyproject.toml`); they were installed into the venv for test collection
only, with no repository change. The Dockerfile installs them explicitly for
the same reason (documented in `docker/README.md` §6).

## 6. Smoke-test steps for the human

Docker cannot run here, so the human performs this (full version in
`docker/README.md` §4):

1. From the repo root: `cp .env.example .env`, fill in your own keys, then
   `docker compose build`.
2. `docker compose up -d`; `docker compose ps` → `db` and `webapp` healthy.
   On failure: `docker compose logs -f webapp`.
3. Open <http://127.0.0.1:8000/documents> — empty sandbox list, no error page.
4. Open <http://127.0.0.1:8000/add> — with `WIKIMIND_LIVE=0` it explains that
   real calls are disabled; to exercise a compile, set `WIKIMIND_LIVE=1` with
   your own `GEMINI_API_KEY` and `OPENROUTER_API_KEY`, `up -d` again, upload a
   small `.txt`/`.md` (≤10 MiB), and confirm the analyze → write → judge job
   page and its shadow-cost/claim summary.
5. Open <http://127.0.0.1:8000/ask> — submit a question; before compiling it
   reports insufficient evidence, after compiling click a citation link and
   confirm claim, anchor quote, trust status and source document.
6. From another machine, `http://<demo-host>:8000` must be unreachable; on the
   demo host `docker compose ps` shows no published port for `db`.

## 7. Evidence manifest (Lane 13 procedure, CRLF, Gate C)

- `SHA256SUMS.txt` covers the six deployment artifacts: `.env.example`,
  `Dockerfile`, `compose.yaml`, `docker/README.md`, `docker/entrypoint.sh`,
  `docker/verification.txt`. It excludes itself and this report (the report
  quotes the manifest digest, so including it would be self-referential).
- Procedure: Lane 13 — `sha256  <path>` lines, sorted by path; written on
  Windows with `Path.write_text(..., newline=None)` so line endings are CRLF.
- Gate C (Ruling 31): the generator asserted `git ls-files --error-unmatch`
  for **every** listed path before writing. Assert passed for all six.
- Measured: `files=6 manifest_sha256=370eec3ab00bbb36f34ca90e3aa0e3bcd11479313f938cea72016d03eb212cfd crlf=6 bare_lf=0`.

## 8. Deviations and notes

1. Docker unavailable → PyYAML structural validation substituted, as the card
   permits. `docker compose config` output could not be produced here.
2. `fastapi`/`uvicorn` are imported by `src/wikimind/webapp/` but undeclared in
   `pyproject.toml` (README.dev.md incorrectly says they are in `dev`). Fixed
   only in the Dockerfile; no `src/` or `pyproject.toml` edit (new-files-only).
3. `.env.example` is matched by the pre-existing `.env.*` ignore rule; it was
   force-added rather than editing `.gitignore`, preserving "new files only"
   and keeping `.env` ignored.
4. In-container `0.0.0.0` bind is required by Docker forwarding (§4) — the
   host-side exposure remains loopback-only.
5. The `embed` extra is not installed; the deterministic `FakeEmbedder` keeps
   the demo offline and small. `WIKIMIND_WEB_EMBEDDER=local` is documented for
   custom builds.

## 9. STOP

Push `lane/21-deploy`; clean tree apart from pre-existing untracked
`.vscode/`, `data/`, `lane19-evidence/`. No merge into `main`, no push of
`main`, no live-corpus access, no Docker smoke test by this lane. Commit SHA is
reported in the handoff message. **STOP.**
