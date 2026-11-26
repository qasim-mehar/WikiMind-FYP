"""Record the Ruling 7 HTTP-429 halt row that the runner deliberately omitted.

The runner writes a manifest row only after a question completes (or a budget
breach); on 429 it halts before appending. This script adds exactly one row for
the halted question from the append-only run.log, with no new attempts.
"""

from __future__ import annotations

import json
from pathlib import Path

EV = Path(__file__).resolve().parent
log = [json.loads(line) for line in (EV / "run.log").read_text(encoding="utf-8").splitlines() if line.strip()]
halted = [r for r in log if r.get("halt_429")]
assert len(halted) == 1, halted
row = halted[0]
mpath = EV / "run_manifest.json"
manifest = json.loads(mpath.read_text(encoding="utf-8"))
if row["id"] not in {m["id"] for m in manifest}:
    q = next(x for x in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"] if x["id"] == row["id"])
    manifest.append(
        {
            "id": row["id"],
            "bucket": q["bucket"],
            "exit": 1,
            "seconds": row["seconds"],
            "attempts": list(range(1, len(row["attempts"]) + 1)),
            "halt_429": True,
            "transient_503": bool(row["transient_503"]),
            "ruling7_halt": True,
        }
    )
    qids = [x["id"] for x in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]]
    manifest.sort(key=lambda m: qids.index(m["id"]))
    mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
print(f"halt={row['id']} manifest_rows={len(manifest)}")
print("unattempted=", [x["id"] for x in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"] if x["id"] not in {m['id'] for m in manifest}])
