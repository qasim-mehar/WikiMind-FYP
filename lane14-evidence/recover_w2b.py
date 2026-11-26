"""Recover w2b rows from the append-only log after the manifest-write bug."""

from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
A = EV / "answers"
log = [
    json.loads(l)
    for l in (EV / "run_w2b.log").read_text(encoding="utf-8").splitlines()
    if l.strip()
]
mpath = EV / "run_manifest.json"
merged = json.loads(mpath.read_text(encoding="utf-8"))
mmap = {r["id"]: r for r in merged}
w2b_path = EV / "run_manifest_w2b.json"
rows = json.loads(w2b_path.read_text(encoding="utf-8")) if w2b_path.exists() else []
rmap = {r["id"]: r for r in rows}

for r in log:
    if r.get("exit") != 0 or r.get("halt_429") or r.get("runaway_attempts"):
        continue
    qid = r["id"]
    n = len(r["attempts"])
    src = A / f"{qid}.attempt{n}.txt"
    if src.exists():
        (A / f"{qid}.txt").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )
    rmap[qid] = r

rows = [rmap[k] for k in rmap]
w2b_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
for qid, r in rmap.items():
    mmap[qid] = r
qids = [
    q["id"] for q in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]
]
ordered = [mmap[q] for q in qids if q in mmap]
mpath.write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
print(f"recovered={list(rmap)} manifest_rows={len(ordered)}")
