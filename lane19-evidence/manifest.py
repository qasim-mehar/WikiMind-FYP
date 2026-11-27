"""Generate the Lane 19 SHA-256 evidence manifest (Ruling 31 / Gate C).

Same CRLF procedure as Lane 13/14. In addition, every listed path MUST be
git-tracked: the generator asserts ``git ls-files --error-unmatch`` for each
one before writing, so a pre-existing .gitignore rule can never silently drop
an evidence file again (the Lane 14 Gate C defect).

Manifest lives at lane19-evidence/SHA256SUMS.txt and lists repo-relative paths
for every Lane 19 evidence artifact, excluding the manifest itself.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "lane19-evidence"
MANIFEST = EV / "SHA256SUMS.txt"

paths = sorted(
    (p for p in EV.rglob("*") if p.is_file() and p.name != MANIFEST.name),
    key=lambda p: str(p.relative_to(ROOT)).replace("\\", "/"),
)
# The halt report records this manifest's digest, so it is deliberately outside
# the manifest to avoid a self-referential hash cycle (Lane 14 did the same
# with LANE14-REPORT.md).
paths += [ROOT / "lane19-costs.jsonl"]
paths.sort(key=lambda p: str(p.relative_to(ROOT)).replace("\\", "/"))

for path in paths:
    rel = path.relative_to(ROOT).as_posix()
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"Gate C assert failed: {rel} is not git-tracked")

lines = []
for path in paths:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = path.relative_to(ROOT).as_posix()
    lines.append(f"{digest}  {rel}")

MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8", newline=None)
raw = MANIFEST.read_bytes()
crlf = raw.count(b"\r\n")
bare_lf = raw.count(b"\n") - crlf
print(
    f"files={len(lines)} manifest_sha256={hashlib.sha256(raw).hexdigest()} "
    f"crlf={crlf} bare_lf={bare_lf}"
)
