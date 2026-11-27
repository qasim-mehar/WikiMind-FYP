"""Generate a SHA-256 manifest of the Lane 20 evidence bundle (Gate C, R31).

Lane 13 CRLF procedure: Path.write_text on Windows writes CRLF, which is
load-bearing for the recorded manifest hash. The manifest excludes itself.
Ruling 31: assert every listed path is git-tracked before writing, so a
.gitignore rule can never silently drop an evidence file.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

EV = Path(__file__).resolve().parent
MANIFEST = EV / "SHA256SUMS.txt"
ROOT = EV.parent

files = sorted(
    (p for p in EV.rglob("*") if p.is_file() and p.name != MANIFEST.name),
    key=lambda p: str(p.relative_to(EV)).replace("\\", "/"),
)

tracked = set(
    subprocess.run(
        ["git", "ls-files", str(EV.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
)
untracked = [
    str(p.relative_to(ROOT)).replace("\\", "/")
    for p in files
    if str(p.relative_to(ROOT)).replace("\\", "/") not in tracked
]
if untracked:
    raise SystemExit(f"Gate C: untracked evidence files: {untracked}")

lines = []
for path in files:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = str(path.relative_to(EV)).replace("\\", "/")
    lines.append(f"{digest}  {rel}")
MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8", newline=None)
raw = MANIFEST.read_bytes()
print(
    f"files={len(lines)} manifest_sha256={hashlib.sha256(raw).hexdigest()} "
    f"crlf={raw.count(bytes([13, 10]))} bare_lf={raw.count(bytes([10])) - raw.count(bytes([13, 10]))}"
)
