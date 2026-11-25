"""Generate a SHA-256 manifest of the Lane 13 evidence directory (Ruling 18)."""

from __future__ import annotations

import hashlib
from pathlib import Path

EV = Path(__file__).resolve().parent
MANIFEST = EV / "SHA256SUMS.txt"

files = sorted(
    (p for p in EV.rglob("*") if p.is_file() and p.name != MANIFEST.name),
    key=lambda p: str(p.relative_to(EV)).replace("\\", "/"),
)
lines = []
for path in files:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rel = str(path.relative_to(EV)).replace("\\", "/")
    lines.append(f"{digest}  {rel}")
MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"files={len(lines)} manifest_sha256={hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}")
