"""Summarise the isolated Lane 13 cost ledger and check for secret leakage."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from wikimind.llm.cost import read_cost_events

EV = Path(__file__).resolve().parent
events = read_cost_events(EV / "costs.jsonl")
by_site: dict[str, list] = defaultdict(list)
for e in events:
    by_site[e.call_site].append(e)
print(f"events={len(events)}")
for site, group in sorted(by_site.items()):
    cost = round(sum(e.shadow_cost_usd for e in group), 12)
    print(f"  {site:<24} calls={len(group):<4} shadow=${cost:.12f}")
print(f"TOTAL shadow=${round(sum(e.shadow_cost_usd for e in events), 12):.12f}")

# Final-answer costs only (one successful answer per question).
ver = json.loads((EV / "verification.json").read_text(encoding="utf-8"))
final = [r["shadow_usd"] for r in ver if r["shadow_usd"] is not None]
print(f"final answers={len(final)} total=${round(sum(final), 12):.12f} max=${max(final):.12f}")

# Leakage scan: no DSN / key material anywhere in evidence text.
needles = ["postgresql://", "localhost:5432/", "AIza", "sk-or-"]
hits = []
for path in EV.rglob("*"):
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle in needles:
            if needle in text:
                hits.append(f"{path.name}:{needle}")
print(f"credential-leak hits={len(hits)} {hits[:10]}")
