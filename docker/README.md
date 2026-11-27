# WikiMind local / demo Docker stack

Repeatable, one-command deployment of the **Lane 17 local web prototype** on a
demo machine. This is a **local developer tool**, not a public deployment and
not the Stage 1 product interface: it has **no authentication**, and it binds to
the host's loopback address only.

The stack is two services:

| Service | Image | Role |
| --- | --- | --- |
| `db` | `pgvector/pgvector:pg16` | Throwaway sandbox Postgres with pgvector |
| `webapp` | built from the repo `Dockerfile` | `python -m wikimind.webapp` (FastAPI, port 8000) |

On first boot the non-root container applies migrations, creates the
`webapp-sandbox` project if needed, and then starts the webapp. Data lives in
two named volumes so it survives `docker compose down` (but not `down -v`).

---

## 1. Quickstart

Prerequisites: Docker Engine 24+ or Docker Desktop with the Compose v2 plugin
(`docker compose`, not the legacy `docker-compose`), and about 1 GB of disk.

```sh
# from the repository root
cp .env.example .env      # then edit .env: add your own keys (see §2)
docker compose up --build -d
docker compose ps                  # both services should be healthy
```

Open <http://127.0.0.1:8000/documents> in a browser on the demo machine.

The **database port is not published at all**, and the webapp port is published
**only on `127.0.0.1`**. From another machine the demo is unreachable by
design. Do not change the left-hand `127.0.0.1` in `compose.yaml` to `0.0.0.0`.

> The page is browsable with placeholder keys. Real model calls stay disabled
> until you set `WIKIMIND_LIVE=1` in `.env`. Add/Ask explain this on the page.

---

## 2. Environment variables

Everything below is read from `.env` (copied from `.env.example`) via Compose's
`env_file`, except the three variables marked **compose-managed**, which are
set in `compose.yaml` and override `.env`. Secrets are supplied **only** as
environment variables; `.env` is gitignored and must never be committed.

| Variable | Purpose | Default in this stack |
| --- | --- | --- |
| `POSTGRES_USER` | Postgres role created in the sandbox database; superuser inside the container. | `wikimind` |
| `POSTGRES_PASSWORD` | Password for that role. Demo placeholder — change it in `.env`. | `CHANGE_ME_DEMO_PASSWORD` |
| `POSTGRES_DB` | Sandbox database name created on first boot. | `wikimind_demo` |
| `WIKIMIND_DB` | DSN used by the entrypoint's `wikimind db migrate` / `project create`. | `postgresql://wikimind:CHANGE_ME_DEMO_PASSWORD@db:5432/wikimind_demo` |
| `WIKIMIND_WEB_DB` | DSN the webapp queries. `WIKIMIND_DB` is only a fallback. | same sandbox DSN as above |
| `WIKIMIND_WEB_PROJECT` | Project name the webapp resolves; created by the entrypoint. | `webapp-sandbox` |
| `WIKIMIND_WEB_COSTS_PATH` | Isolated JSONL shadow-cost ledger (kept in the `webapp_data` volume). | `/app/data/webapp/costs.jsonl` |
| `WIKIMIND_WEB_UPLOAD_DIR` | Directory for uploaded/selected source files (same volume). | `/app/data/webapp/uploads` |
| `WIKIMIND_WEB_EMBEDDER` | `fake` = deterministic keyless embedder (no model download); `local` = learned embedder, needs the `embed` extra. | `fake` |
| `WIKIMIND_WEB_HOST` | **compose-managed.** Container bind address. `0.0.0.0` is required for Docker's port forwarding; it is *not* a public exposure. | `0.0.0.0` |
| `WIKIMIND_WEB_PORT` | **compose-managed.** Container listen port; matches the published port. | `8000` |
| `WIKIMIND_WEB_PUBLIC` | **compose-managed.** App guard waiver required for a non-loopback in-container bind. Host publication remains loopback-only. | `1` |
| `WIKIMIND_LIVE` | `1` enables real, metered model calls for Add/Ask. `0` keeps the demo at $0 spend. | `0` |
| `GEMINI_API_KEY` | Drafter key (Gemini API). Required for Add/Ask when `WIKIMIND_LIVE=1`. | `REPLACE_WITH_YOUR_GEMINI_KEY` |
| `OPENROUTER_API_KEY` | Judge key (OpenRouter). Required for the compile judge when live. | `REPLACE_WITH_YOUR_OPENROUTER_KEY` |
| `WIKIMIND_GROBID_URL` | Optional GROBID endpoint for academic PDFs; empty uses the offline parser. | empty |
| `WIKIMIND_ENV_FILE` | Config loader path override. Not needed here (Compose injects `.env` itself). | `.env` |
| `WIKIMIND_COSTS_PATH` | Root-CLI cost ledger path. Not used by the webapp, which uses `WIKIMIND_WEB_COSTS_PATH`. | `costs.jsonl` at CWD |
| `WIKIMIND_COMPILE_DELAY` | Optional inter-call delay for live compilation; useful when rate-limited. | unset |
| `WIKIMIND_COST_DB_MIRROR` | **Never set in this stack.** Opt-in mirror of cost events into `cost_events`; the entrypoint refuses to start if it is non-empty. | unset (absent) |

Model pins and prices live in `src/wikimind/prices.py`; the demo does not
override them.

---

## 3. Live-corpus warning — READ BEFORE EDITING THE DSN

> **The default target is the disposable sandbox database `wikimind_demo`, not
> the protected live corpus `wikimind_live_l3`.**
>
> Do **not** point `WIKIMIND_DB` or `WIKIMIND_WEB_DB` at `wikimind_live_l3`.
> The application refuses to start against that database by name, and the
> entrypoint enforces the same guard before the CLI bootstrap runs. Any change
> that makes the demo query or mutate the live corpus is an explicit,
> human-configured opt-in — it is never a default of this stack, and it
> requires a separate decision outside this lane.
>
> Also leave `WIKIMIND_COST_DB_MIRROR` unset. Pointing this stack at the live
> corpus while the cost mirror is armed would append cost rows to that corpus;
> the entrypoint stops startup if the mirror variable is present.

The sandbox is a throwaway: compiled pages, claims, uploads and the webapp cost
ledger are all local to this stack's volumes.

---

## 4. Documented smoke test (human-performed)

Docker is unavailable in the packaging environment, so the human executes
this. Do not run the live corpus.

1. **Build.** From the repository root, with `.env` created from `.env.example`:
   ```sh
   docker compose build
   ```
   Expect a clean build with no `pip` errors.
2. **Up.** `docker compose up -d`, then `docker compose ps` — both `db` and
   `webapp` are `healthy`. If the webapp is unhealthy, check
   `docker compose logs -f webapp`.
3. **Documents page.** Open <http://127.0.0.1:8000/documents>. On a fresh
   sandbox it shows an empty document list (no error page, no DSN in the text).
4. **Add page.** Open <http://127.0.0.1:8000/add>. With `WIKIMIND_LIVE=0` it
   explains that real model calls are disabled; pages remain browsable.
   To exercise a compile, set `WIKIMIND_LIVE=1` in `.env` after inserting your
   own `GEMINI_API_KEY` and `OPENROUTER_API_KEY`, then `docker compose up -d`
   again. Upload a small `.txt`/`.md` file (max 10 MiB) or select one already in
   `WIKIMIND_WEB_UPLOAD_DIR`, watch the job page's analyze → write → judge
   stages, and confirm the shadow-cost/claim summary appears.
5. **Ask page.** Open <http://127.0.0.1:8000/ask> and submit a question. If
   nothing has been compiled, the app reports insufficient evidence rather
   than fabricating an answer. After a compile, click a citation link and
   confirm the claim, anchor quote, trust status and source document are shown.
6. **Security spot-check.** From a *different* machine, try
   `http://<demo-host>:8000` — it must be unreachable. On the demo host,
   `docker compose ps` must show **no** published port for `db`.

---

## 5. Teardown, backup, restore

```sh
docker compose down          # stop and remove containers; VOLUMES ARE KEPT
docker compose down -v       # DESTROY the database and webapp data volumes
docker compose logs -f webapp db
docker compose exec webapp sh          # shell as the non-root wikimind user
```

**Back up the sandbox database** (from the repo root, output file is local):

```sh
docker compose exec -T db pg_dump -U wikimind wikimind_demo > wikimind_demo.sql
```

Change `-U` if you changed `POSTGRES_USER`, and the database name if you
changed `POSTGRES_DB`. Restore into a fresh stack with:

```sh
docker compose exec -T db psql -U wikimind -d wikimind_demo < wikimind_demo.sql
```

**Back up the webapp ledger and uploads** (both live in `webapp_data`):

```sh
docker run --rm -v wikimind-demo_webapp_data:/data -v "$PWD":/backup \
  alpine tar czf /backup/webapp-data.tgz -C /data .
```

The volume name uses the Compose project name `wikimind-demo`; run
`docker volume ls` if you overrode it with `-p`. Backups are ordinary files —
store them outside the container and do not commit them.

---

## 6. Implementation notes

- The image installs the project from `pyproject.toml` (base dependencies plus
  the `llm` extra) and installs `fastapi`/`uvicorn` explicitly: the webapp
  imports them, but they are not declared in any `pyproject` extra on this
  base. No `src/` file or `pyproject.toml` is changed by this lane.
- `sentence-transformers` (the `embed` extra) is not installed, so the webapp
  uses its deterministic `FakeEmbedder`. That keeps the image small and avoids
  a model download at demo time; retrieval still works with the same embedder
  on both compile and query. Set `WIKIMIND_WEB_EMBEDDER=local` only in a custom
  build that installs the `embed` extra.
- Entrypoint, migrations and README live under `docker/` / `migrations/`; the
  database migration hashes are unchanged.
- Validation performed without Docker: `compose.yaml` parsed with
  `yaml.safe_load` (PyYAML), plus the git-tracked CRLF evidence manifest in
  `SHA256SUMS.txt`. See `LANE21-REPORT.md` for exact results and test counts.
