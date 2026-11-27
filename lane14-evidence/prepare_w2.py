"""W2 prep: archive window-1 evidence for the 43 questions being re-run.

Nothing is deleted from the evidence set: window-1 attempt files and final
files are copied into answers/window1/ with their original names, and the
window-1 manifest is copied to run_manifest_w1.json. The window-2 runner then
writes fresh attempt files at the top level, so both windows remain visible.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

EV = Path(__file__).resolve().parent
A = EV / "answers"
ARCH = A / "window1"
ARCH.mkdir(parents=True, exist_ok=True)

qids = [
    q["id"] for q in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]
]
w1 = json.loads((EV / "run_manifest.json").read_text(encoding="utf-8"))
w1map = {r["id"]: r for r in w1}

reopened = [r["id"] for r in w1 if r["exit"] != 0]
unattempted = [q for q in qids if q not in w1map]
targets = sorted(set(reopened + unattempted), key=qids.index)
assert len(targets) == 43, (len(reopened), len(unattempted), len(targets))

moved = []
for qid in targets:
    for p in sorted(A.glob(f"{qid}*.txt")):
        dst = ARCH / p.name
        shutil.copy2(p, dst)
        moved.append(p.name)

shutil.copy2(EV / "run_manifest.json", EV / "run_manifest_w1.json")
(EV / "w2_targets.json").write_text(
    json.dumps({"reopened": reopened, "unattempted": unattempted, "targets": targets}, indent=2),
    encoding="utf-8",
    newline="\n",
)
print(
    f"targets={len(targets)} reopened={len(reopened)} unattempted={len(unattempted)} archived_files={len(moved)}"
)
