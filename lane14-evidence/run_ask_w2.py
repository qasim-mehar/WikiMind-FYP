"""Phase B W2: second-window run of the 43 questions without a final answer.

Per governance direction after the Ruling 7 halt:
  - 31 unanswered (C020 + C021-C030 + U001-U020);
  - 12 attempt-exhausted questions re-opened with a FRESH 6-attempt budget:
    M023, M056, M058, M059, M060, C001-C007 (unconditional, no selection).
  - append to the SAME isolated costs.jsonl;
  - WIKIMIND_COST_DB_MIRROR removed; WIKIMIND_LIVE=1;
  - production retrieval defaults (no flags);
  - real HTTP 429 halts immediately again;
  - bounded backoff 20/40/60/90/120 s, max 6 attempts per question in W2.

Window-1 files for these questions are archived under answers/window1/ and the
window-1 manifest is run_manifest_w1.json. The merged run_manifest.json records
attempts_w1, attempts_w2 and total_attempts for every question.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from live import ROOT, live_dsn

EV = Path(__file__).resolve().parent
A = EV / "answers"
COSTS = EV / "costs.jsonl"
EXE = ROOT / ".venv" / "Scripts" / "wikimind.exe"

IS_429 = re.compile(
    r"\b429\s+(?:RESOURCE_EXHAUSTED|Too Many Requests)\b"
    r'|"code"\s*:\s*429\b|\bHTTP\s+429\b|\b429\s+rate\s+limit',
    re.IGNORECASE,
)
IS_503 = re.compile(r'\b503\s+UNAVAILABLE\b|"code"\s*:\s*503\b|\bhigh demand\b', re.IGNORECASE)
SHADOW = re.compile(r"shadow:\s*\$([\d.]+)")
BACKOFF = (20, 40, 60, 90, 120)
PER_QUESTION_CAP = 0.02
TOTAL_CAP = 5.00

QUESTIONS = json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]
QTEXT = {q["id"]: q["question"] for q in QUESTIONS}
QIDS = [q["id"] for q in QUESTIONS]
W1 = json.loads((EV / "run_manifest_w1.json").read_text(encoding="utf-8"))
W1MAP = {r["id"]: r for r in W1}
TARGETS = json.loads((EV / "w2_targets.json").read_text(encoding="utf-8"))["targets"]


def shadows(qid: str | None = None) -> float:
    pat = f"{qid}.attempt*.txt" if qid else "*.attempt*.txt"
    total = 0.0
    for p in A.glob(pat):
        m = SHADOW.search(p.read_text(encoding="utf-8", errors="replace"))
        if m:
            total += float(m.group(1))
    return total


def merged(rows: dict[str, dict]) -> list[dict]:
    out = []
    for qid in QIDS:
        if qid in rows:
            out.append(rows[qid])
        elif qid in W1MAP:
            r = dict(W1MAP[qid])
            r.setdefault("window", 1)
            r.setdefault("attempts_w1", len(r["attempts"]))
            r.setdefault("attempts_w2", 0)
            r.setdefault("total_attempts", len(r["attempts"]))
            out.append(r)
    return out


def write_manifest(rows: dict[str, dict]) -> None:
    (EV / "run_manifest.json").write_text(
        json.dumps(merged(rows), indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )


def main() -> None:
    only = os.environ.get("WIKIMIND_L14_ONLY", "").strip()
    targets = [q for q in TARGETS if not only or q in {x.strip() for x in only.split(",")}]
    A.mkdir(parents=True, exist_ok=True)
    # Remove stale top-level attempt files for targets (archived under window1/).
    for qid in targets:
        for p in A.glob(f"{qid}.attempt*.txt"):
            p.unlink()

    env = os.environ.copy()
    env.pop("WIKIMIND_COST_DB_MIRROR", None)
    env["WIKIMIND_LIVE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    dsn = live_dsn()  # never printed

    prior_path = EV / "run_manifest_w2.json"
    prior: dict[str, dict] = {}
    if prior_path.exists():
        prior = {r["id"]: r for r in json.loads(prior_path.read_text(encoding="utf-8"))}
    log = (EV / "run_w2.log").open("a", encoding="utf-8", newline="\n")
    rows = dict(prior)
    for qid in targets:
        if qid in prior and prior[qid].get("exit") == 0 and not prior[qid].get("halt_429"):
            print(json.dumps({"id": qid, "skip": "already successful in W2"}), flush=True)
            continue
        attempts: list[int] = []
        row = None
        for attempt in range(len(BACKOFF) + 1):
            t0 = time.perf_counter()
            proc = subprocess.run(
                [str(EXE), "ask", QTEXT[qid], "--dsn", dsn, "--costs-path", str(COSTS)],
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
            (A / f"{qid}.attempt{attempt + 1}.txt").write_text(
                combined, encoding="utf-8", newline="\n"
            )
            attempts.append(attempt + 1)
            row = {
                "id": qid,
                "window": 2,
                "exit": proc.returncode,
                "seconds": elapsed,
                "attempts": list(attempts),
                "attempts_w1": len(W1MAP.get(qid, {}).get("attempts", [])),
                "attempts_w2": len(attempts),
                "total_attempts": len(W1MAP.get(qid, {}).get("attempts", [])) + len(attempts),
                "halt_429": bool(IS_429.search(combined)),
                "transient_503": bool(IS_503.search(combined)),
            }
            log.write(json.dumps(row) + "\n")
            log.flush()
            print(json.dumps(row), flush=True)
            if row["halt_429"]:
                log.write(json.dumps({"halt": "HTTP 429 — Ruling 7; window 2 halt"}) + "\n")
                log.flush()
                rows[qid] = row
                write_manifest(rows)
                (EV / "run_manifest_w2.json").write_text(
                    json.dumps(list(rows.values()), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                    newline="\n",
                )
                print(json.dumps({"halt": "HTTP 429 — Ruling 7; window 2 halt"}), flush=True)
                return
            if proc.returncode == 0:
                break
            if attempt < len(BACKOFF):
                time.sleep(BACKOFF[attempt])

        assert row is not None
        if shadows(qid) > PER_QUESTION_CAP:
            print(json.dumps({"halt": "per-question budget breach", "id": qid}), flush=True)
            rows[qid] = row
            write_manifest(rows)
            return
        if shadows() > TOTAL_CAP:
            print(json.dumps({"halt": "total budget breach"}), flush=True)
            rows[qid] = row
            write_manifest(rows)
            return
        src = A / f"{qid}.attempt{len(attempts)}.txt"
        (A / f"{qid}.txt").write_text(
            src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8", newline="\n"
        )
        rows[qid] = row
        write_manifest(rows)
        (EV / "run_manifest_w2.json").write_text(
            json.dumps(list(rows.values()), indent=2, ensure_ascii=False),
            encoding="utf-8",
            newline="\n",
        )
    log.close()


if __name__ == "__main__":
    main()
