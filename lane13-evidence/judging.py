"""Compact human-judging digest from verification.json."""

from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
rows = json.loads((EV / "verification.json").read_text(encoding="utf-8"))
lines = []
for r in rows:
    lines.append(
        f"\n===== {r['id']} [{r['kind']}] suff={r['sufficient']} cites={r['citations']} ====="
    )
    lines.append(f"A: {r['answer']}")
    for c in r["cited"]:
        lines.append(f"  C: {c['claim']}")
(EV / "judging.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
