"""Shared report shape for integrity checks.

Every command returns the same shape: a count per named check, a list of human
findings, and optional machine-readable rows.  ``--json`` on the CLI emits
:meth:`IntegrityReport.to_dict` verbatim; exit codes are 0 (clean) / 1
(findings), matching the V1/V4/V6 card contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IntegrityReport:
    """Result of one integrity command."""

    name: str
    counts: dict[str, int] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def count(self, key: str, value: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + value

    def set_count(self, key: str, value: int) -> None:
        self.counts[key] = value

    def finding(self, message: str) -> None:
        self.findings.append(message)

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.name,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "counts": dict(sorted(self.counts.items())),
            "findings": list(self.findings),
            "rows": list(self.rows),
        }

    def render(self) -> str:
        status = "clean" if self.ok else f"{len(self.findings)} finding(s)"
        lines = [f"{self.name}: {status}"]
        for key, value in sorted(self.counts.items()):
            lines.append(f"  {key}: {value}")
        for finding in self.findings:
            lines.append(f"  - {finding}")
        return "\n".join(lines)
