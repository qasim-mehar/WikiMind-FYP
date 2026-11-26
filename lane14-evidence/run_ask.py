"""Phase B2/B3: drive the real `wikimind ask` CLI for the Lane 14 V0 eval.

Every call uses WIKIMIND_LIVE=1, the pinned drafter, a DSN override to the live
corpus, a dedicated isolated evidence ledger, and PRODUCTION retrieval defaults
(no --top-k / --hops / --max-claims / --max-attempts flags are passed).

Guards (work card B3/B4):
  - max 6 attempts per question, backoff 20/40/60/90/120 s;
  - any REAL HTTP 429 halts the whole run immediately (Ruling 7);
  - transient 503s retry within the bounds above;
  - per-question shadow <= $0.02 and total <= $5.00; breach halts;
  - WIKIMIND_COST_DB_MIRROR is removed from the child environment.

stdout/stderr of each attempt are persisted under answers/. The run manifest
records per-question attempt counts.
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

# HTTP-pattern-only matching: the benign HF "higher rate limits" warning is NOT
# a 429 (Lane 13 DEVIATIONS item 2). Require an actual HTTP error shape.
IS_429 = re.compile(
    r'\b429\s+(?:RESOURCE_EXHAUSTED|Too Many Requests)\b'
    r'|"code"\s*:\s*429\b|\bHTTP\s+429\b|\b429\s+rate\s+limit',
    re.IGNORECASE,
)
IS_503 = re.compile(r'\b503\s+UNAVAILABLE\b|"code"\s*:\s*503\b|\bhigh demand\b', re.IGNORECASE)
SHADOW = re.compile(r"shadow:\s*\$([\d.]+)")
BACKOFF = (20, 40, 60, 90, 120)
PER_QUESTION_CAP = 0.02
TOTAL_CAP = 5.00


def shadows_for(qid: str) -> list[float]:
    out = []
    for p in sorted(ANSWERS.glob(f"{qid}.attempt*.txt")):
        m = SHADOW.search(p.read_text(encoding="utf-8", errors="replace"))
        if m:
            out.append(float(m.group(1)))
    return out


def total_spend() -> float:
    return sum(sum(shadows_for(q["id"])) for q in QUESTIONS)


QUESTIONS = json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    only = os.environ.get("WIKIMIND_L14_ONLY", "").strip()
    questions = QUESTIONS
    if only:
        wanted = {x.strip() for x in only.split(",") if x.strip()}
        questions = [q for q in questions if q["id"] in wanted]
    ANSWERS.mkdir(parents=True, exist_ok=True)

    prior_path = EV / "run_manifest.json"
    prior: dict[str, dict] = {}
    if prior_path.exists():
        prior = {row["id"]: row for row in json.loads(prior_path.read_text(encoding="utf-8"))}

    env = os.environ.copy()
    env.pop("WIKIMIND_COST_DB_MIRROR", None)  # guard: never mirror to live corpus
    env["WIKIMIND_LIVE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    dsn = live_dsn()  # never printed

    log = (EV / "run.log").open("a", encoding="utf-8", newline="\n")
    manifest = []
    for index, q in enumerate(questions, start=1):
        qid = q["id"]
        if index < start:
            continue
        if qid in prior and prior[qid].get("exit") != 0 and len(prior[qid].get("attempts", [])) >= len(BACKOFF) + 1:
            print(json.dumps({"id": qid, "skip": "attempt cap reached (6) — provider failure retained"}), flush=True)
            continue
        if qid in prior and prior[qid].get("exit") == 0 and not prior[qid].get("halt_429"):
            print(json.dumps({"id": qid, "skip": "already successful"}), flush=True)
            continue
        attempts: list[int] = []
        row = None
        for attempt in range(len(BACKOFF) + 1):
            t0 = time.perf_counter()
            proc = subprocess.run(
                [str(EXE), "ask", q["question"], "--dsn", dsn, "--costs-path", str(COSTS)],
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
            (ANSWERS / f"{qid}.attempt{attempt + 1}.txt").write_text(
                combined, encoding="utf-8", newline="\n"
            )
            attempts.append(attempt + 1)
            row = {
                "id": qid,
                "bucket": q["bucket"],
                "exit": proc.returncode,
                "seconds": elapsed,
                "attempts": list(attempts),
                "halt_429": bool(IS_429.search(combined)),
                "transient_503": bool(IS_503.search(combined)),
            }
            log.write(json.dumps(row) + "\n")
            log.flush()
            print(json.dumps(row), flush=True)
            if row["halt_429"]:
                log.write(json.dumps({"halt": "HTTP 429 — Ruling 7; scope unchanged"}) + "\n")
                log.flush()
                _write_manifest(prior, manifest)
                print(json.dumps({"halt": "HTTP 429 — Ruling 7; scope unchanged"}), flush=True)
                return
            if proc.returncode == 0:
                break
            if attempt < len(BACKOFF):
                time.sleep(BACKOFF[attempt])

        assert row is not None
        q_shadow = sum(shadows_for(qid))
        if q_shadow > PER_QUESTION_CAP:
            print(json.dumps({"halt": "per-question budget breach", "id": qid, "shadow": q_shadow}), flush=True)
            _write_manifest(prior, manifest + [row])
            return
        if total_spend() > TOTAL_CAP:
            print(json.dumps({"halt": "total budget breach", "shadow": total_spend()}), flush=True)
            _write_manifest(prior, manifest + [row])
            return

        src = ANSWERS / f"{qid}.attempt{len(attempts)}.txt"
        (ANSWERS / f"{qid}.txt").write_text(
            src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8", newline="\n"
        )
        manifest.append(row)
        _write_manifest(prior, manifest)
        if limit and len(manifest) >= limit:
            break
    log.close()


def _write_manifest(prior: dict[str, dict], rows: list[dict]) -> None:
    merged = {**prior, **{r["id"]: r for r in rows}}
    ordered = [merged[q["id"]] for q in QUESTIONS if q["id"] in merged]
    (EV / "run_manifest.json").write_text(
        json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
