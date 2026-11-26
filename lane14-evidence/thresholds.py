"""B6: two accuracy views, attempt histogram, bootstrap CI, threshold table."""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

EV = Path(__file__).resolve().parent
verification = json.loads((EV / "verification.json").read_text(encoding="utf-8"))
verdicts = json.loads((EV / "verdicts.json").read_text(encoding="utf-8"))
manifest = json.loads((EV / "run_manifest.json").read_text(encoding="utf-8"))
m3b_path = EV / "m3b_judgments.json"
m3b = json.loads(m3b_path.read_text(encoding="utf-8")) if m3b_path.exists() else {}
costs = json.loads((EV / "cost_report.json").read_text(encoding="utf-8"))

N_TOTAL = 150
mmap = {r["id"]: r for r in manifest}
vmap = {v["id"]: v for v in verdicts}
answered = [
    r for r in verification if r.get("sufficient") is not None and not r.get("provider_failure")
]
answered_ids = {r["id"] for r in answered}
n_answered = len(answered_ids)
n_unanswered = N_TOTAL - n_answered

# Conservative: every non-answered question is a FAIL. Engine-capability: only
# answered questions are in the denominator; provider exhaustion reported apart.
correct_all = sum(1 for v in verdicts if v["verdict"] == "PASS")
acc_conservative = correct_all / N_TOTAL
acc_engine = correct_all / n_answered if n_answered else 0.0

rng = random.Random(7)


def ci(flags: list[int], n: int) -> tuple[float, float]:
    boots = []
    for _ in range(10000):
        boots.append(sum(rng.choice(flags) for _ in range(n)) / n)
    boots.sort()
    return boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots)) - 1]


flags_all = [
    1 if vmap[qid]["verdict"] == "PASS" else 0
    for qid in [
        q["id"]
        for q in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]
    ]
]
lo_a, hi_a = ci(flags_all, N_TOTAL)
lo_e, hi_e = (
    ci(
        [1 for v in verdicts if v["verdict"] == "PASS"] + [0] * (n_answered - correct_all),
        n_answered,
    )
    if n_answered
    else (0, 0)
)

by_bucket: dict[str, list[int]] = defaultdict(list)
for v in verdicts:
    by_bucket[v["bucket"]].append(1 if v["verdict"] == "PASS" else 0)

shadows = [r["shadow_usd"] or 0 for r in verification]
per_query = sum(shadows) / n_answered if n_answered else 0.0
graded_total = costs["total_graded_usd"]
allin_total = costs["total_all_inclusive_usd"]
per_query_graded = graded_total / n_answered if n_answered else 0.0
per_query_allin = allin_total / n_answered if n_answered else 0.0

cites = [c for r in verification for c in r["cited"]]
anchored = sum(1 for c in cites if c["anchored"])
m3a_citations = anchored / len(cites) if cites else 0.0
m3a_answers = sum(1 for r in verification if r["verified"]) / n_answered if n_answered else 0.0
citing_answers = [r for r in verification if r["citations"] > 0]
m3a_citing = (
    sum(1 for r in citing_answers if r["verified"]) / len(citing_answers) if citing_answers else 0.0
)

m3b_rate = m3b.get("entailment_rate")
m3b_status = "N/A"
if m3b.get("computable") and m3b_rate is not None:
    m3b_status = (
        "PASS" if m3b_rate >= 0.90 else ("KILL(<80%)" if m3b_rate < 0.80 else "FAIL(80-90%)")
    )

attempt_hist = Counter(r.get("total_attempts", len(r["attempts"])) for r in manifest)
wall_total = sum(r.get("seconds", 0) for r in manifest)
provider_exhausted = sum(1 for r in manifest if r["exit"] != 0 and not r.get("halt_429"))
halt_429 = sum(1 for r in manifest if r.get("halt_429"))

rows = [
    (
        "M1",
        "Paired accuracy difference (multi-hop + contradiction)",
        "N/A",
        "single-arm run; no strong-RAG baseline arm exists to pair against",
        "N/A",
    ),
    (
        "M2",
        "Paired accuracy difference (single-hop)",
        "N/A",
        "single-arm run; no paired difference / lower bound L computable",
        "N/A",
    ),
    (
        "M3a",
        "Anchor validity (machine citation verification)",
        (
            f"{m3a_citations:.4f} of cited claims anchored; {m3a_citing:.4f} of {len(citing_answers)} citing answers verified "
            f"({len(verification) - len(citing_answers)} zero-citation answers graded FAIL, not vacuous passes)"
        ),
        "Ruling 25 machine verification is the M3a check (resolution + slug + anchor containment)",
        "PASS" if m3a_citing >= 0.95 and m3a_citations >= 0.95 else "FAIL",
    ),
    (
        "M3b",
        "Entailment faithfulness (non-Gemini blind judge)",
        f"{m3b_rate:.4f} ({m3b.get('judgments', 0)} judgments)" if m3b_rate is not None else "N/A",
        m3b.get("halt")
        or (
            "judge sample via pinned non-Gemini OpenRouter model"
            if m3b_rate is not None
            else "no judge result"
        ),
        m3b_status,
    ),
    (
        "M4",
        "Amortized cost per paper (40-50 papers, drills, curve)",
        "N/A",
        "corpus is 4 fixture documents; no 40-50 paper corpus, removal drill or repair pass",
        "N/A",
    ),
    (
        "M5",
        "Cost per query",
        (
            f"graded ${per_query_graded:.8f} mean (total ${graded_total:.8f}); "
            f"all-inclusive ${per_query_allin:.8f} mean (total ${allin_total:.8f})"
        ),
        "gate <= $0.02; target <= $0.01. Graded excludes Ruling 30 anomalies; all-inclusive is real spend",
        "PASS" if per_query_graded <= 0.02 else "FAIL",
    ),
    (
        "M6",
        "Propagation errors on hand-built oracle",
        "N/A",
        "no hand-built oracle / 5 removal drills were run in this single-arm evidence pass",
        "N/A",
    ),
    ("M7", "Interviews confirming pain", "N/A", "no interviews were conducted in this run", "N/A"),
    (
        "M8",
        "Share of L2 content reducing to L1 claim IDs",
        "N/A",
        "corpus has 0 ledger_blocks; no L2 ledger content to measure",
        "N/A",
    ),
]

lines = [
    "# Lane 14 — V0 threshold table (single-arm, two-window evidence run)",
    "",
    "## Accuracy views",
    "",
    (
        f"- Conservative (all 150; unanswered counted FAIL): **{acc_conservative:.4f}** "
        f"({correct_all}/{N_TOTAL}), 95% bootstrap CI {lo_a:.4f}–{hi_a:.4f}."
    ),
    (
        f"- Engine-capability (answered only; provider exhaustion separate): **{acc_engine:.4f}** "
        f"({correct_all}/{n_answered}), 95% bootstrap CI {lo_e:.4f}–{hi_e:.4f}."
    ),
    (
        f"- Answered: {n_answered}; unanswered (provider exhaustion in both windows): {n_unanswered}; "
        f"halt_429 rows: {halt_429}."
    ),
    f"- Provider-exhausted rows (exit != 0, non-429): {provider_exhausted}.",
    "- Per-bucket (conservative denominators over 150 question IDs): "
    + ", ".join(f"{b}={sum(v)}/{len(v)}" for b, v in sorted(by_bucket.items())),
    f"- Verdict counts: {dict(Counter(v['verdict'] for v in verdicts))}.",
    "",
    "## Cost accounting (Ruling 30 — two views, nothing hidden)",
    "",
    f"- Graded (anomalies excluded): ${graded_total:.12f} total, ${per_query_graded:.8f}/answered query.",
    f"- All-inclusive (real spend): ${allin_total:.12f} total, ${per_query_allin:.8f}/answered query.",
    (
        f"- Anomaly spend: ${costs['total_anomaly_usd']:.12f} across {costs['anomaly_events']} event(s) "
        f"(cap ${costs['anomaly_cap_usd']:.2f}: **{costs['anomaly_cap_status']}**)."
    ),
    "",
    "## Attempts and wall time (both windows)",
    "",
    f"- Attempt histogram (total attempts per question): {dict(sorted(attempt_hist.items()))}.",
    (
        f"- Total recorded attempt wall time: {wall_total:.1f} s ({wall_total / 3600:.2f} h), "
        "excluding backoff sleeps."
    ),
    "",
    "## Threshold table",
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
    (
        "M1 is the outcome-statistic gate and is N/A in a single-arm run, so no "
        "WIN/PARITY/INCONCLUSIVE/LOSS row is selected. This run is directional "
        "evidence only (D14); the full two-arm evaluation remains ahead."
    ),
    "",
    (
        f"Gate note: M5 target ($0.01) {'met' if per_query <= 0.01 else 'not met'}; "
        f"gate ($0.02) {'met' if per_query <= 0.02 else 'breached'}."
    ),
]
(EV / "THRESHOLDS.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print("\n".join(lines[:12]))
