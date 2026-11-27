"""Generate the Lane 18 evidence SHA-256 manifest (Lane 13 procedure, Ruling 31).

Same CRLF procedure as Lanes 13/14: ``Path.write_text`` on Windows writes CRLF,
which is load-bearing for the recorded manifest hash.  The manifest itself is
excluded.  Ruling 31 (Gate C) adds a hard precondition: every listed path MUST
be git-tracked before the manifest is written, so a pre-existing gitignore rule
(e.g. ``costs.jsonl``) can never silently drop an evidence file.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

EV = Path(__file__).resolve().parent
ROOT = EV.parent
MANIFEST = EV / "SHA256SUMS.txt"


def repo_path(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


files = sorted(
    (p for p in EV.rglob("*") if p.is_file() and p.name != MANIFEST.name),
    key=lambda p: str(p.relative_to(EV)).replace("\\", "/"),
)

# Ruling 31 Gate C: assert git tracking BEFORE writing anything.
tracked = set(
    subprocess.check_output(
        ["git", "ls-files", "--", repo_path(EV)],
        cwd=ROOT,
        text=True,
    ).splitlines()
)
untracked = [repo_path(path) for path in files if repo_path(path) not in tracked]
if untracked:
    raise SystemExit("evidence files are not git-tracked: " + ", ".join(untracked))

lines: list[str] = []
for path in files:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = str(path.relative_to(EV)).replace("\\", "/")
    lines.append(f"{digest}  {rel}")
MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8", newline=None)

raw = MANIFEST.read_bytes()
print(
    f"files={len(lines)} manifest_sha256={hashlib.sha256(raw).hexdigest()} "
    f"crlf={raw.count(bytes([13, 10]))} "
    f"bare_lf={raw.count(bytes([10])) - raw.count(bytes([13, 10]))}"
)
