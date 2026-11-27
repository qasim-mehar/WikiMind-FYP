"""Apply manual Ruling 25 verdict overrides to verdicts_draft.json.

The draft scorer is deterministic but conservative; every REVIEW row is
reviewed by hand against the frozen key, answer text and cited claims in
judging.txt. Manual decisions are recorded here as overrides with reasons.
"""

from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
draft = json.loads((EV / "verdicts_draft.json").read_text(encoding="utf-8"))
override_path = EV / "verdict_overrides.json"
overrides = json.loads(override_path.read_text(encoding="utf-8")) if override_path.exists() else {}

final = []
for row in draft:
    item = dict(row)
    oid = row["id"]
    if oid in overrides:
        item["verdict"] = overrides[oid]["verdict"]
        item["manual_reason"] = overrides[oid]["reason"]
    else:
        item["verdict"] = "PASS" if row["draft"] == "PASS" else row["draft"]
        item["manual_reason"] = ""
    final.append(item)

(EV / "verdicts.json").write_text(
    json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
)
from collections import Counter

print(dict(Counter(v["verdict"] for v in final)))
