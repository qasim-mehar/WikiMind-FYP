"""Assemble LANE14-REPORT.md from the two-window Phase B evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

EV = Path(__file__).resolve().parent
ROOT = EV.parent
verification = json.loads((EV / "verification.json").read_text(encoding="utf-8"))
verdicts = json.loads((EV / "verdicts.json").read_text(encoding="utf-8"))
key = json.loads((EV / "answer_key.json").read_text(encoding="utf-8"))
manifest = json.loads((EV / "run_manifest.json").read_text(encoding="utf-8"))
boundary = json.loads((EV / "window_boundary.json").read_text(encoding="utf-8"))
before = json.loads((EV / "corpus_proof_before.json").read_text(encoding="utf-8"))
after = json.loads((EV / "corpus_proof_after.json").read_text(encoding="utf-8"))
m3b = json.loads((EV / "m3b_judgments.json").read_text(encoding="utf-8"))
costs = json.loads((EV / "cost_report.json").read_text(encoding="utf-8"))
thresholds = (EV / "THRESHOLDS.md").read_text(encoding="utf-8")

N = 150
correct = sum(1 for v in verdicts if v["verdict"] == "PASS")
answered = [r for r in verification if not r.get("provider_failure")]
n_answered = len(answered)
shadows = sum(r["shadow_usd"] or 0 for r in verification)
judge_shadow = m3b.get("shadow_cost_usd", 0)
retr = Counter(r["retrieval"] for r in verification)
attempt_hist = Counter(r.get("total_attempts", len(r["attempts"])) for r in manifest)
wall = sum(r.get("seconds", 0) for r in manifest)
manifest_hash = hashlib.sha256((EV / "SHA256SUMS.txt").read_bytes()).hexdigest()

lines = [
    "# LANE 14 — FULL V0 EVALUATION (single-arm, two-window evidence run)",
    "",
    "## 1. Pinned base and scope",
    "",
    "- Pinned run base: `f45a67ce31a9ed1be52200ec77494bdcffd29b2b` (ancestors 8d4a165, 8b4a6c1 confirmed).",
    "- Branch `lane/14-v0-eval`; worktree `C:\\Users\\mianq\\Projects\\WikiMind-L14`.",
    "- Live corpus `wikimind_live_l3` targeted only by DSN override, session forced `default_transaction_read_only = on`.",
    "- `src/`, `tests/`, and all frozen files unchanged on this lane.",
    "- Single arm: real `wikimind ask` CLI, `WIKIMIND_LIVE=1`, production retrieval defaults (no forced settings).",
    "",
    "## 2. Windows",
    "",
    f"- Window 1 halt: {boundary['window_1']['halt_pacific']} (C020 real HTTP 429, Ruling 7).",
    f"- Window 2 resume: {boundary['window_2']['resume_pacific']} after midnight-Pacific quota reset.",
    f"- W2 scope: {boundary['window_2']['scope']}.",
    "",
    "## 3. Question set and answer key",
    "",
    f"- Frozen question SHA256: `{key['questions_sha256']}`; 150 questions (40 S / 60 M / 30 C / 20 U).",
    "- Full set and key: `lane14-evidence/questions.json`, `answer_key.json`, `PHASE-A-REPORT.md`. Unchanged.",
    "- Authoring spend $0.00.",
    "",
    "## 4. Corpus-count proof (read-only session)",
    "",
    "| Table | Before | After |",
    "| --- | ---: | ---: |",
]
for table in before:
    if table in ("phase", "session", "read_only_session", "max_nodes_created_at"):
        continue
    lines.append(f"| {table} | {before[table]} | {after.get(table)} |")
lines += [
    f"| max_nodes_created_at | {before['max_nodes_created_at']} | {after.get('max_nodes_created_at')} |",
    "",
]

lines += [
    "## 5. Run, cost, attempts",
    "",
    f"- Answered: {n_answered}/150; unanswered after both windows: {150 - n_answered}.",
    f"- Attempt histogram (total attempts per question, both windows): {dict(sorted(attempt_hist.items()))}.",
    f"- Total attempt wall time (excludes backoff sleeps): {wall:.1f} s ({wall / 3600:.2f} h).",
    f"- Retrieval lines reported: {dict(retr)} (production defaults).",
    (
        f"- Answer ledger graded (Ruling 30, anomalies excluded): ${costs['total_graded_usd']:.12f}; "
        f"all-inclusive (real spend): ${costs['total_all_inclusive_usd']:.12f}."
    ),
    (
        f"- Anomaly spend: ${costs['total_anomaly_usd']:.12f} across {costs['anomaly_events']} event(s); "
        f"cap ${costs['anomaly_cap_usd']:.2f} = {costs['anomaly_cap_status']}."
    ),
    (
        f"- M3b judge ledger: ${judge_shadow:.12f}; combined all-inclusive spend: "
        f"${costs['total_all_inclusive_usd'] + judge_shadow:.12f} (cap $5.00)."
    ),
    "- `WIKIMIND_COST_DB_MIRROR` unset throughout; ledger isolated; `cost_events` unchanged.",
    "",
    "## 6. Judging (Ruling 25 rubric against frozen key)",
    "",
    f"- Conservative accuracy (all 150; unanswered = FAIL): {correct}/150 = {correct / 150:.4f}.",
    f"- Engine-capability accuracy (answered only): {correct}/{n_answered} = {correct / n_answered:.4f}.",
    "- Machine citation verification: `verification.json` (resolution, slug, anchor containment, prose refs).",
    f"- Non-Gemini judge sample (M3b): {m3b.get('judgments', 0)} judgments, rate={m3b.get('entailment_rate')}.",
    "",
    "## 7. Threshold table",
    "",
    thresholds,
    "",
    "## 8. Deviations",
    "",
    "See `lane14-evidence/DEVIATIONS.md` (Phase A A-1..A-4; Phase B B-1..B-9).",
    "",
    "## 9. Evidence manifest",
    "",
    "- `lane14-evidence/SHA256SUMS.txt` (CRLF, Lane 13 procedure).",
    f"- Manifest SHA256: `{manifest_hash}`.",
    "",
    "## 10. STOP",
    "",
    "Merging into main and the V0 scoping decision are human actions. No merge, no main push, no further corpus writes.",
]
(ROOT / "LANE14-REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(f"wrote LANE14-REPORT.md; manifest_sha256={manifest_hash}")
