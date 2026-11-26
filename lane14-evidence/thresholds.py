"""B6: threshold-by-threshold table, bootstrap CI, and bucket accuracy."""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

EV = Path(__file__).resolve().parent
verification = json.loads((EV / "verification.json").read_text(encoding="utf-8"))
verdicts = json.loads((EV / "verdicts.json").read_text(encoding="utf-8"))
m3b_path = EV / "m3b_judgments.json"
m3b = json.loads(m3b_path.read_text(encoding="utf-8")) if m3b_path.exists() else {}

n = len(verification)
correct = [1 if v["verdict"] == "PASS" else 0 for v in verdicts]
acc = sum(correct) / n

rng = random.Random(7)
boots = []
for _ in range(10000):
    s = sum(rng.choice(correct) for _ in range(n)) / n
    boots.append(s)
boots.sort()
lo = boots[int(0.025 * len(boots))]
hi = boots[int(0.975 * len(boots)) - 1]

by_bucket: dict[str, list[int]] = defaultdict(list)
for v in verdicts:
    by_bucket[v["bucket"]].append(1 if v["verdict"] == "PASS" else 0)

shadows = [r["shadow_usd"] or 0 for r in verification]
per_query = sum(shadows) / n

# M3a: fraction of citations that resolve, slug-match, are anchored, and whose
# answer has no prose-ref problems.
cites = [c for r in verification for c in r["cited"]]
anchored = sum(1 for c in cites if c["anchored"])
m3a_citations = anchored / len(cites) if cites else 0.0
m3a_answers = sum(1 for r in verification if r["verified"]) / n if n else 0.0

m3b_rate = m3b.get("entailment_rate")
m3b_status = "N/A"
if m3b.get("computable") and m3b_rate is not None:
    m3b_status = "PASS" if m3b_rate >= 0.90 else ("KILL(<80%)" if m3b_rate < 0.80 else "FAIL(80-90%)")

rows = [
    ("M1", "Paired accuracy difference (multi-hop + contradiction)", "N/A",
     "single-arm run; no strong-RAG baseline arm exists to pair against", "N/A"),
    ("M2", "Paired accuracy difference (single-hop)", "N/A",
     "single-arm run; no paired difference / lower bound L computable", "N/A"),
    ("M3a", "Anchor validity (machine citation verification)",
     f"{m3a_answers:.4f} answers verified; {m3a_citations:.4f} citations anchored",
     "Ruling 25 machine verification is the M3a check (resolution + slug + anchor containment)",
     "PASS" if m3a_answers >= 0.95 and m3a_citations >= 0.95 else "FAIL"),
    ("M3b", "Entailment faithfulness (non-Gemini blind judge)",
     f"{m3b_rate:.4f} ({m3b.get('judgments', 0)} judgments)" if m3b_rate is not None else "N/A",
     m3b.get("halt") or ("judge sample via pinned non-Gemini OpenRouter model" if m3b_rate is not None else "no non-Gemini judge result"),
     m3b_status),
    ("M4", "Amortized cost per paper (40-50 papers, drills, curve)", "N/A",
     "corpus is 4 fixture documents; no 40-50 paper corpus, removal drill or repair pass", "N/A"),
    ("M5", "Cost per query", f"${per_query:.8f} mean; ${sum(shadows):.8f} total / {n}",
     "gate <= $0.02; target <= $0.01 (shadow, production list prices)",
     "PASS" if per_query <= 0.02 else "FAIL"),
    ("M6", "Propagation errors on hand-built oracle", "N/A",
     "no hand-built oracle / 5 removal drills were run in this single-arm evidence pass", "N/A"),
    ("M7", "Interviews confirming pain", "N/A",
     "no interviews were conducted in this run", "N/A"),
    ("M8", "Share of L2 content reducing to L1 claim IDs", "N/A",
     "corpus has 0 ledger_blocks; no L2 ledger content to measure", "N/A"),
]

lines = [
    "# Lane 14 — V0 threshold table (single-arm evidence run)",
    "",
    f"- Questions answered: {n} (one pass, production defaults)",
    f"- Overall accuracy: {acc:.4f} (95% bootstrap CI {lo:.4f}–{hi:.4f}, 10,000 resamples, seed 7)",
    f"- Per-bucket: " + ", ".join(
        f"{b}={sum(v)/len(v):.4f} (n={len(v)})" for b, v in sorted(by_bucket.items())
    ),
    f"- Verdict counts: {dict(Counter(v['verdict'] for v in verdicts))}",
    "",
    "| Metric | Measured value | Reason / basis | Result |",
    "| --- | --- | --- | --- |",
]
for m, what, val, reason, status in rows:
    lines.append(f"| {m} — {what} | {val} | {reason} | **{status}** |")
lines += [
    "",
    "## Decision rule",
    "",
    "M1 is the outcome-statistic gate and is N/A in a single-arm run, so no "
    "WIN/PARITY/INCONCLUSIVE/LOSS row is selected. This run is directional "
    "evidence only (D14); the full two-arm evaluation remains ahead.",
    "",
    f"Gate note: M5 target ($0.01) {'met' if per_query <= 0.01 else 'not met'}; "
    f"gate ($0.02) {'met' if per_query <= 0.02 else 'breached'}.",
]
(EV / "THRESHOLDS.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print("\n".join(lines))
