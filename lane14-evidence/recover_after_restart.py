"""Recover the in-flight answer lost when the server restarted the batch.

Rebuilds the manifest from the append-only run.log for any question whose final
logged attempt succeeded but whose manifest row was not yet written. Never
adds attempts and never changes already-recorded rows.
"""

from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
QIDS = [q["id"] for q in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]]
log = [json.loads(line) for line in (EV / "run.log").read_text(encoding="utf-8").splitlines() if line.strip()]
mpath = EV / "run_manifest.json"
manifest = json.loads(mpath.read_text(encoding="utf-8"))
mmap = {row["id"]: row for row in manifest}

recovered = []
by_qid: dict[str, list[dict]] = {}
for row in log:
    by_qid.setdefault(row["id"], []).append(row)

for qid, rows in by_qid.items():
    if qid in mmap:
        continue
    successful = [r for r in rows if r["exit"] == 0]
    if not successful:
        continue
    last = successful[-1]
    n = len(rows[-1]["attempts"])
    src = EV / "answers" / f"{qid}.attempt{n}.txt"
    if not src.exists():
        continue
    (EV / "answers" / f"{qid}.txt").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    mmap[qid] = {
        "id": qid,
        "bucket": last["bucket"],
        "exit": 0,
        "seconds": last["seconds"],
        "attempts": list(range(1, n + 1)),
        "halt_429": bool(last["halt_429"]),
        "transient_503": bool(last["transient_503"]),
        "recovered_after_restart": True,
    }
    recovered.append(qid)

ordered = [mmap[qid] for qid in QIDS if qid in mmap]
mpath.write_text(
    json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
)
print(f"recovered={recovered} manifest_rows={len(ordered)}")
