"""Record the W2 per-question budget-breach halt in the window boundary file."""

from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
p = EV / "window_boundary.json"
b = json.loads(p.read_text(encoding="utf-8"))
b["window_2"]["halt"] = {
    "question": "M023",
    "kind": "per-question shadow budget breach (W5)",
    "shadow_usd": 0.10386925,
    "cap_usd": 0.02,
    "cause": "one qa.answer call emitted 65520 output tokens (shadow $0.10074675) followed by a 434-token retry; 4 ledger events total",
    "halted_utc": "2026-11-26T08:37:56Z",
    "halted_pacific": "2026-11-26T00:37:56-08:00",
    "halt_429": False,
    "questions_completed_in_w2": 1,
    "remaining_unattempted": 30,
    "note": "No second 429 occurred. Governance direction W5 makes a budget breach a halt condition; await direction (third window or explicit waiver).",
}
p.write_text(json.dumps(b, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
print(json.dumps(b["window_2"]["halt"], indent=2))
