"""Resume window 2 under Governance Ruling 30 (runaway-attempt amendment).

Runs every question that still has no valid final answer, with production
defaults, bounded attempts, and real-429 / non-anomalous-budget / total-budget /
anomaly-budget halts. A runaway attempt (>10,000 output tokens in a single
qa.answer ledger event) is discarded and does NOT count against the per-question
cap; it is recorded in runaway_log.json with its ledger index and cost.
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
BACKOFF = (20, 40, 60, 90, 120)
PER_QUESTION_CAP = 0.02
TOTAL_CAP = 5.00
ANOMALY_CAP = 0.50
RUNAWAY_TOKENS = 10000

QUESTIONS = json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]
QTEXT = {q["id"]: q["question"] for q in QUESTIONS}
QIDS = [q["id"] for q in QUESTIONS]
MANIFEST_PATH = EV / "run_manifest.json"
manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
MMAP = {r["id"]: r for r in manifest}
W1MAP = {r["id"]: r for r in json.loads((EV / "run_manifest_w1.json").read_text(encoding="utf-8"))}

# Questions with no valid final answer.
TARGETS = [qid for qid in QIDS if MMAP.get(qid, {}).get("exit") != 0]
BUDGET = {}
for qid in TARGETS:
    prior = len(MMAP.get(qid, {}).get("attempts", []))
    # C020 had one W1 attempt; keep its history and give 5 more in this window.
    BUDGET[qid] = 5 if qid == "C020" else 6

RUNAWAY_PATH = EV / "runaway_events.json"
runaway = json.loads(RUNAWAY_PATH.read_text(encoding="utf-8"))
anomaly_spend = float(runaway["cumulative_anomaly_spend_usd"])


def events() -> list[dict]:
    out = []
    with COSTS.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_manifest(rows: dict[str, dict]) -> None:
    ordered = [
        rows[qid] if qid in rows else MMAP[qid] for qid in QIDS if qid in rows or qid in MMAP
    ]
    MANIFEST_PATH.write_text(
        json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    (EV / "run_manifest_w2b.json").write_text(
        json.dumps([rows[qid] for qid in QIDS if qid in rows], indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    global anomaly_spend
    only = os.environ.get("WIKIMIND_L14_ONLY", "").strip()
    targets = [q for q in TARGETS if not only or q in {x.strip() for x in only.split(",")}]
    prior_path = EV / "run_manifest_w2b.json"
    rows: dict[str, dict] = {}
    if prior_path.exists():
        rows = {r["id"]: r for r in json.loads(prior_path.read_text(encoding="utf-8"))}

    env = os.environ.copy()
    env.pop("WIKIMIND_COST_DB_MIRROR", None)
    env["WIKIMIND_LIVE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    dsn = live_dsn()

    log = (EV / "run_w2b.log").open("a", encoding="utf-8", newline="\n")
    for qid in targets:
        if rows.get(qid, {}).get("exit") == 0:
            print(json.dumps({"id": qid, "skip": "already valid in this segment"}), flush=True)
            continue
        attempts: list[int] = []
        normal_shadow = 0.0
        q_anomalies = []
        row = None
        for attempt in range(BUDGET[qid]):
            n0 = len(events())
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

            new_events = events()[n0:]
            runaway_here = False
            for j, e in enumerate(new_events):
                idx = n0 + j + 1
                if (
                    e.get("call_site") == "qa.answer"
                    and int(e.get("out_tokens", 0)) > RUNAWAY_TOKENS
                ):
                    runaway_here = True
                    anomaly_spend += float(e.get("shadow_cost_usd", 0))
                    q_anomalies.append(
                        {
                            "question": qid,
                            "attempt": attempt + 1,
                            "ledger_index_1based": idx,
                            "in_tokens": e.get("in_tokens"),
                            "out_tokens": e.get("out_tokens"),
                            "shadow_cost_usd": e.get("shadow_cost_usd"),
                        }
                    )
                else:
                    normal_shadow += float(e.get("shadow_cost_usd", 0))

            row = {
                "id": qid,
                "window": 2,
                "segment": "w2b",
                "exit": 0 if proc.returncode == 0 and not runaway_here else 1,
                "seconds": elapsed,
                "attempts": list(attempts),
                "attempts_w1": len(W1MAP.get(qid, {}).get("attempts", [])),
                "attempts_w2": len(attempts),
                "total_attempts": len(W1MAP.get(qid, {}).get("attempts", [])) + len(attempts),
                "halt_429": bool(IS_429.search(combined)),
                "transient_503": bool(IS_503.search(combined)),
                "runaway_attempts": len(q_anomalies),
                "normal_shadow_usd": round(normal_shadow, 12),
            }
            log.write(json.dumps(row) + "\n")
            log.flush()
            print(json.dumps(row), flush=True)

            if row["halt_429"]:
                log.write(json.dumps({"halt": "HTTP 429 — Ruling 7; window 2b halt"}) + "\n")
                log.flush()
                rows[qid] = row
                write_manifest(rows)
                _save_runaway(runaway, q_anomalies, anomaly_spend)
                print(json.dumps({"halt": "HTTP 429 — Ruling 7; window 2b halt"}), flush=True)
                return
            if anomaly_spend > ANOMALY_CAP:
                print(json.dumps({"halt": "anomaly spend cap", "usd": anomaly_spend}), flush=True)
                rows[qid] = row
                write_manifest(rows)
                _save_runaway(runaway, q_anomalies, anomaly_spend)
                return
            if normal_shadow > PER_QUESTION_CAP:
                print(
                    json.dumps({"halt": "non-anomalous per-question budget breach", "id": qid}),
                    flush=True,
                )
                rows[qid] = row
                write_manifest(rows)
                _save_runaway(runaway, q_anomalies, anomaly_spend)
                return
            if sum(e.get("shadow_cost_usd", 0) for e in events()) > TOTAL_CAP:
                print(json.dumps({"halt": "total budget breach"}), flush=True)
                rows[qid] = row
                write_manifest(rows)
                _save_runaway(runaway, q_anomalies, anomaly_spend)
                return
            if row["exit"] == 0:
                src = A / f"{qid}.attempt{attempt + 1}.txt"
                (A / f"{qid}.txt").write_text(
                    src.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                    newline="\n",
                )
                break
            if attempt < BUDGET[qid] - 1:
                time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])

        rows[qid] = row
        write_manifest(rows)
        _save_runaway(runaway, q_anomalies, anomaly_spend)
    log.close()


def _save_runaway(data: dict, new: list[dict], total: float) -> None:
    data.setdefault("attempts", []).extend(new)
    data["cumulative_anomaly_spend_usd"] = round(total, 12)
    RUNAWAY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
