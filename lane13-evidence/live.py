"""Lane 13 read-only helper for the wikimind_live_l3 corpus.

The live DSN is derived from .env at runtime (Ruling 15: .env stays on
wikimind_l0; DSN override is the only sanctioned targeting method).  Every
connection is forced into a read-only session as defense-in-depth.
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

COUNT_TABLES = ("documents", "claims", "edges", "compile_units", "cost_events")


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


if __name__ == "__main__":
    with connect_live() as conn:
        c = counts(conn)
        for table in COUNT_TABLES:
            print(f"{table}={c[table]}")
