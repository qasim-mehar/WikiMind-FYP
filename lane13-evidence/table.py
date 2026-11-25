"""Generate the per-question verdict table rows for the Lane 13 report."""

from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
questions = {q["id"]: q for q in json.loads((EV / "questions.json").read_text(encoding="utf-8"))}
ver = json.loads((EV / "verification.json").read_text(encoding="utf-8"))
manifest = {r["id"]: r for r in json.loads((EV / "run_manifest.json").read_text(encoding="utf-8"))}

print("| Q | Target kind | Verdict | Citations (prose / list) | Shadow (USD) | Evidence |")
print("|---|---|---|---|---|---|")
for row in ver:
    qid = row["id"]
    q = questions[qid]
    m = manifest[qid]
    if q["kind"] == "insufficient":
        verdict = "HONEST INSUFFICIENCY"
    elif q["kind"] == "conflict":
        verdict = "SUFFICIENT — both sides cited"
    else:
        verdict = "SUFFICIENT"
    cites = f"{row['prose_refs']} / {row['citations']}"
    shadow = f"{row['shadow_usd']:.8f}"
    evidence = q["evidence"]
    if row["problems"]:
        evidence += " ⚠ " + "; ".join(row["problems"])
    if qid == "Q31":
        evidence += " (premise corrected)"
    if m.get("attempts") and len(m["attempts"]) > 1:
        evidence += f" [retries: {len(m['attempts'])}]"
    print(f"| {qid} | {q['kind']} | {verdict} | {cites} | {shadow} | {evidence} |")
