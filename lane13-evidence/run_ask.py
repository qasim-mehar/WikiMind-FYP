"""Drive the real `wikimind ask` CLI for the Lane 13 mini-check.

Every call uses WIKIMIND_LIVE=1, the pinned drafter, a DSN override to the
live corpus, and a dedicated evidence cost ledger.  stdout/stderr of each
call are persisted under answers/.  The runner halts on HTTP 429.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from live import ROOT, live_dsn

EV = Path(__file__).resolve().parent
ANSWERS = EV / "answers"
COSTS = EV / "costs.jsonl"
EXE = ROOT / ".venv" / "Scripts" / "wikimind.exe"


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    questions = json.loads((EV / "questions.json").read_text(encoding="utf-8"))
    only = os.environ.get("WIKIMIND_L13_ONLY", "").strip()
    if only:
        wanted = {x.strip() for x in only.split(",") if x.strip()}
        questions = [q for q in questions if q["id"] in wanted]
    ANSWERS.mkdir(parents=True, exist_ok=True)
    prior_path = EV / "run_manifest.json"
    prior: dict[str, dict] = {}
    if prior_path.exists():
        prior = {row["id"]: row for row in json.loads(prior_path.read_text(encoding="utf-8"))}
    env = os.environ.copy()
    env["WIKIMIND_LIVE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    dsn = live_dsn()  # never printed

    is_429 = re.compile(
        r'429\s+(?:RESOURCE_EXHAUSTED|Too Many Requests)|"code"\s*:\s*429|HTTP\s+429', re.IGNORECASE
    )
    is_503 = re.compile(r'503\s+UNAVAILABLE|"code"\s*:\s*503|high demand', re.IGNORECASE)
    backoff = (20, 40, 60, 90, 120)

    manifest = []
    for index, q in enumerate(questions, start=1):
        qid = q["id"]
        if index < start:
            continue
        if qid in prior and prior[qid].get("exit") == 0 and not prior[qid].get("halt_429"):
            print(json.dumps({"id": qid, "skip": "already successful"}), flush=True)
            continue
        attempts = []
        row = None
        for attempt in range(len(backoff) + 1):
            t0 = time.perf_counter()
            proc = subprocess.run(
                [
                    str(EXE),
                    "ask",
                    q["question"],
                    "--dsn",
                    dsn,
                    "--costs-path",
                    str(COSTS),
                ],
                env=env,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=ROOT,
            )
            elapsed = round(time.perf_counter() - t0, 3)
            combined = proc.stdout + "\n" + proc.stderr
            (ANSWERS / f"{qid}.attempt{attempt + 1}.txt").write_text(combined, encoding="utf-8")
            attempts.append(attempt + 1)
            row = {
                "id": qid,
                "kind": q["kind"],
                "exit": proc.returncode,
                "seconds": elapsed,
                "attempts": attempts,
                "halt_429": bool(is_429.search(combined)),
                "transient_503": bool(is_503.search(combined)),
            }
            if row["halt_429"] or proc.returncode == 0:
                break
            if attempt < len(backoff):
                time.sleep(backoff[attempt])
        (ANSWERS / f"{qid}.txt").write_text(
            (ANSWERS / f"{qid}.attempt{len(attempts)}.txt").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        manifest.append(row)
        print(json.dumps(row), flush=True)
        if row["halt_429"]:
            print(
                json.dumps({"halt": "HTTP 429 — schedule stretches (Ruling 7); scope unchanged"}),
                flush=True,
            )
            break
        if limit and len(manifest) >= limit:
            break
    merged = {**prior, **{row["id"]: row for row in manifest}}
    all_questions = json.loads((EV / "questions.json").read_text(encoding="utf-8"))
    ordered = [merged[q["id"]] for q in all_questions if q["id"] in merged]
    (EV / "run_manifest.json").write_text(
        json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
