"""Lane 14 read-only helper for the wikimind_live_l3 corpus.

The live DSN is derived from .env at runtime (Ruling 15: .env stays on
wikimind_l0; DSN override is the only sanctioned targeting method).  Every
connection is forced into a read-only session as defense-in-depth.

Adapted verbatim from Lane 13's lane13-evidence/live.py; the count set is
extended for the Lane 14 before/after corpus-count proof (work card B1/B7).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from psycopg import connect
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wikimind.config import load_dotenv

COUNT_TABLES = (
    "documents",
    "claims",
    "edges",
    "compile_units",
    "cost_events",
    "nodes",
    "node_versions",
    "events",
    "ledger_blocks",
    "jobs",
)


def live_dsn() -> str:
    load_dotenv(ROOT / ".env")
    dsn = os.environ.get("WIKIMIND_DB", "")
    if not dsn:
        raise RuntimeError("no WIKIMIND_DB in .env")
    live, n = re.subn(r"/wikimind_l0$", "/wikimind_live_l3", dsn)
    if n != 1:
        raise RuntimeError("could not derive live DSN from .env")
    return live


def connect_live():
    conn = connect(live_dsn(), row_factory=dict_row, autocommit=True)
    conn.execute("SET default_transaction_read_only = on")
    return conn


def counts(conn) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in COUNT_TABLES:
        out[table] = int(conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"])
    return out


def proof() -> dict:
    with connect_live() as conn:
        assert (
            conn.execute("SHOW default_transaction_read_only").fetchone()[
                "default_transaction_read_only"
            ]
            == "on"
        )
        out = counts(conn)
        out["max_nodes_created_at"] = str(
            conn.execute("SELECT max(created_at) AS t FROM nodes").fetchone()["t"]
        )
        out["read_only_session"] = "on"
        return out


if __name__ == "__main__":
    proof_data = proof()
    for key, value in proof_data.items():
        print(f"{key}={value}")
