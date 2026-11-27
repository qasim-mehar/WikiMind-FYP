"""Ruling 30 cost accounting: graded (excluding anomalies) vs all-inclusive.

Anomaly events are identified by 1-based ledger index in runaway_events.json
(the M023 baseline plus any attempts classified during the resumed run).
Per-question figures come from the CLI `shadow:` line of each attempt file,
minus that question's anomaly events.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EV = Path(__file__).resolve().parent
A = EV / "answers"
SHADOW = re.compile(r"shadow:\s*\$([\d.]+)")
QUESTIONS = json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]
events = [
    json.loads(line)
    for line in (EV / "costs.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
runaway = json.loads((EV / "runaway_events.json").read_text(encoding="utf-8"))

anomaly_indices: set[int] = set()
anomaly_by_q: dict[str, float] = {}
for e in runaway.get("baseline", []):
    anomaly_indices.add(int(e["ledger_index_1based"]))
    anomaly_by_q[e["question"]] = anomaly_by_q.get(e["question"], 0) + float(e["shadow_cost_usd"])
for e in runaway.get("attempts", []):
    anomaly_indices.add(int(e["ledger_index_1based"]))
    anomaly_by_q[e["question"]] = anomaly_by_q.get(e["question"], 0) + float(e["shadow_cost_usd"])

total_all = sum(float(e.get("shadow_cost_usd", 0)) for e in events)
total_anomaly = sum(float(e["shadow_cost_usd"]) for e in runaway.get("baseline", [])) + sum(
    float(e["shadow_cost_usd"]) for e in runaway.get("attempts", [])
)
total_graded = total_all - total_anomaly

per_q = {}
for q in QUESTIONS:
    qid = q["id"]
    allin = 0.0
    for p in A.glob(f"{qid}.attempt*.txt"):
        m = SHADOW.search(p.read_text(encoding="utf-8", errors="replace"))
        if m:
            allin += float(m.group(1))
    per_q[qid] = {
        "all_inclusive_usd": round(allin, 12),
        "anomaly_usd": round(anomaly_by_q.get(qid, 0), 12),
        "graded_usd": round(allin - anomaly_by_q.get(qid, 0), 12),
    }

out = {
    "ruling": "Ruling 30",
    "ledger_events": len(events),
    "anomaly_event_indices_1based": sorted(anomaly_indices),
    "anomaly_events": len(anomaly_indices),
    "total_all_inclusive_usd": round(total_all, 12),
    "total_anomaly_usd": round(total_anomaly, 12),
    "total_graded_usd": round(total_graded, 12),
    "per_question_graded_sum_usd": round(sum(v["graded_usd"] for v in per_q.values()), 12),
    "per_question_allin_sum_usd": round(sum(v["all_inclusive_usd"] for v in per_q.values()), 12),
    "unattributed_usd": round(total_all - sum(v["all_inclusive_usd"] for v in per_q.values()), 12),
    "unattributed_note": "one pre-batch S001 smoke qa.answer event remains in the shared ledger; the retained S001 final answer is the later batch event. Headline totals are whole-ledger real spend; per-question figures come from answer attempt files.",
    "anomaly_cap_usd": 0.5,
    "anomaly_cap_status": "PASS" if total_anomaly <= 0.5 else "BREACH",
    "per_question": per_q,
}
(EV / "cost_report.json").write_text(
    json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
)
print(
    json.dumps(
        {
            k: out[k]
            for k in (
                "ledger_events",
                "anomaly_events",
                "total_all_inclusive_usd",
                "total_anomaly_usd",
                "total_graded_usd",
                "anomaly_cap_status",
            )
        },
        indent=2,
    )
)
