"""Lane 19: build the dev/sandbox probe corpus and compile it once.

The live corpus (wikimind_live_l3) is never touched. A dedicated sandbox
database is created from the local pgvector template, migrated, seeded with six
source documents authored for this lane, and compiled through the real engine
with the pinned drafter/judge. All LLM spend goes to the isolated lane ledger
``lane19-costs.jsonl``; WIKIMIND_COST_DB_MIRROR is removed from the environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from psycopg import connect as pg_connect
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from wikimind.compile.graph import create_project, list_projects, register_document
from wikimind.compile.pipeline import compile_project
from wikimind.config import load_dotenv
from wikimind.db.connection import with_dbname
from wikimind.db.migrations import run_migrations
from wikimind.db.reset import template_exists
from wikimind.integrity.costs import build_meter
from wikimind.llm.gemini import GeminiClient
from wikimind.llm.judge import JudgeClient
from wikimind.parse import parse_file

SANDBOX = "wikimind_l19_sandbox"
PROJECT = "lane19-probe"
CORPUS = ROOT / "lane19-evidence" / "corpus"
LEDGER = ROOT / "lane19-costs.jsonl"


def admin_dsn(base: str) -> str:
    params = conninfo_to_dict(base)
    password = params.pop("password", None)
    if password:
        os.environ["PGPASSWORD"] = password
    return make_conninfo(**params)


def main() -> None:
    load_dotenv(ROOT / ".env")
    os.environ.pop("WIKIMIND_COST_DB_MIRROR", None)
    os.environ["WIKIMIND_LIVE"] = "1"
    base = os.environ["WIKIMIND_DB"]

    with pg_connect(admin_dsn(base), autocommit=True, connect_timeout=5) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (SANDBOX,)
        ).fetchone()
        if not exists:
            if template_exists(admin):
                admin.execute(
                    SQL("CREATE DATABASE {} TEMPLATE {}").format(
                        Identifier(SANDBOX), Identifier("wikimind_template")
                    )
                )
            else:
                admin.execute(SQL("CREATE DATABASE {}").format(Identifier(SANDBOX)))
            print(f"created sandbox database {SANDBOX}")
        else:
            print(f"reusing sandbox database {SANDBOX}")

    dsn = with_dbname(base, SANDBOX)
    applied = run_migrations(dsn, ROOT / "migrations")
    print(f"migrations applied now: {len(applied)}")

    conn = pg_connect(dsn, row_factory=dict_row, autocommit=True)
    try:
        projects = [p for p in list_projects(conn) if p.name == PROJECT]
        project = projects[0] if projects else create_project(conn, PROJECT)
        print(f"project {project.name} id={project.id}")

        for path in sorted(CORPUS.glob("*.txt")):
            parsed = parse_file(path, parser="text")
            _doc_id, created = register_document(conn, project.id, parsed, path.name)
            print(f"  document {path.name}: {len(parsed.channels)} channel(s), created={created}")

        import time

        from wikimind.llm.errors import RetryExhaustedError

        result = None
        for attempt in range(1, 11):
            meter = build_meter(LEDGER)
            try:
                result = compile_project(
                    conn,
                    project=project,
                    drafter=GeminiClient(meter=meter, temperature=0.0),
                    judge=JudgeClient(meter=meter, temperature=0.0),
                    min_score=0,
                    delay=float(os.environ.get("WIKIMIND_COMPILE_DELAY", "0")),
                    meter=meter,
                )
                print(f"compile succeeded on attempt {attempt}", flush=True)
                break
            except RetryExhaustedError as exc:
                print(
                    f"compile attempt {attempt} failed on transient provider error: {exc}",
                    flush=True,
                )
                if attempt == 10:
                    raise
                time.sleep(min(10 * attempt, 60))
        assert result is not None
        stats = result.stats
        print(
            f"compile status={result.status} units={stats.units_seen} "
            f"claims_committed={stats.claims_committed} judge_rejected={stats.judge_rejected}"
        )
        print(f"compile shadow cost ${result.run_cost_usd:.12f}")
        counts = {
            table: int(conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"])
            for table in ("documents", "claims", "nodes", "edges", "node_versions", "embeddings")
        }
        print("counts:", counts)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
