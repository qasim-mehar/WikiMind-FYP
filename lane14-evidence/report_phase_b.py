"""Assemble LANE14-REPORT.md from the Phase B evidence artifacts."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

EV = Path(__file__).resolve().parent
ROOT = EV.parent
verification = json.loads((EV / "verification.json").read_text(encoding="utf-8"))
verdicts = json.loads((EV / "verdicts.json").read_text(encoding="utf-8"))
key = json.loads((EV / "answer_key.json").read_text(encoding="utf-8"))
manifest_rows = json.loads((EV / "run_manifest.json").read_text(encoding="utf-8"))
before = json.loads((EV / "corpus_proof_before.json").read_text(encoding="utf-8"))
after = json.loads((EV / "corpus_proof_after.json").read_text(encoding="utf-8"))
m3b_path = EV / "m3b_judgments.json"
m3b = json.loads(m3b_path.read_text(encoding="utf-8")) if m3b_path.exists() else {}
thresholds = (EV / "THRESHOLDS.md").read_text(encoding="utf-8")

n = len(verification)
correct = [1 if v["verdict"] == "PASS" else 0 for v in verdicts]
acc = sum(correct) / n
by_bucket: dict[str, list[int]] = defaultdict(list)
for v in verdicts:
    by_bucket[v["bucket"]].append(1 if v["verdict"] == "PASS" else 0)
shadows = [r["shadow_usd"] or 0 for r in verification]
retr = Counter(r["retrieval"] for r in verification)
attempts = [len(r["attempts"]) for r in manifest_rows]
m = (EV / "SHA256SUMS.txt").read_bytes()
import hashlib

manifest_hash = hashlib.sha256(m).hexdigest()

lines = [
    "# LANE 14 — FULL V0 EVALUATION (single-arm evidence run)",
    "",
    "## 1. Pinned base and scope",
    "",
    "- Pinned run base: `f45a67ce31a9ed1be52200ec77494bdcffd29b2b` (ancestors 8d4a165, 8b4a6c1 confirmed).",
    "- Branch `lane/14-v0-eval`; worktree `C:\\Users\\mianq\\Projects\\WikiMind-L14`.",
    "- Live corpus `wikimind_live_l3` targeted only by DSN override, session forced `default_transaction_read_only = on`.",
    "- `src/`, `tests/`, and all frozen files unchanged on this lane.",
    "- Single arm: real `wikimind ask` CLI, `WIKIMIND_LIVE=1`, production retrieval defaults (no forced settings).",
    "",
    "## 2. Question set and answer key",
    "",
    f"- Frozen question SHA256: `{key['questions_sha256']}`; 150 questions (40 S / 60 M / 30 C / 20 U).",
    "- Full set and key: `lane14-evidence/questions.json`, `lane14-evidence/answer_key.json`, `PHASE-A-REPORT.md`.",
    "- Authoring spend $0.00 (zero LLM calls).",
    "",
    "## 3. Corpus-count proof (read-only session)",
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
    "## 4. Run and cost",
    "",
    f"- Questions answered: {n}; one pass; per-question attempts: min={min(attempts)}, max={max(attempts)}, total={sum(attempts)}.",
    f"- Retrieval lines reported: {dict(retr)}.",
    f"- Shadow total (answer ledger): ${sum(shadows):.12f}; mean per query ${sum(shadows)/n:.8f}.",
    f"- M3b judge ledger: ${m3b.get('shadow_cost_usd', 0):.12f}." if m3b else "- M3b judge ledger: not run.",
    f"- Combined shadow spend (answer + judge): ${sum(shadows) + m3b.get('shadow_cost_usd', 0):.12f} (cap $5.00).",
    "- `WIKIMIND_COST_DB_MIRROR` unset throughout; ledger isolated to `lane14-evidence/`; corpus `cost_events` unchanged.",
    "",
    "## 5. Judging (Ruling 25 rubric against frozen key)",
    "",
    f"- Overall accuracy: **{acc:.4f}** ({sum(correct)}/{n}).",
    "- By bucket: " + ", ".join(
        f"{b}={sum(v)/len(v):.4f} ({sum(v)}/{len(v)})" for b, v in sorted(by_bucket.items())
    ),
    f"- Verdict counts: {dict(Counter(v['verdict'] for v in verdicts))}.",
    "- Machine citation verification: see `verification.json` (resolution, slug match, anchor containment, prose refs).",
    f"- Non-Gemini judge sample (M3b): {m3b.get('judgments', 'not run')} judgments, rate={m3b.get('entailment_rate')}."
    if m3b
    else "- M3b: NOT COMPUTABLE.",
    "",
    "## 6. Threshold table",
    "",
    thresholds,
    "",
    "## 7. Deviations",
    "",
    "See `lane14-evidence/DEVIATIONS.md` (Phase A entries A-1..A-4 plus Phase B append).",
    "",
    "## 8. Evidence manifest",
    "",
    f"- `lane14-evidence/SHA256SUMS.txt` (CRLF, Lane 13 procedure).",
    f"- Manifest SHA256: `{manifest_hash}`.",
    f"- Manifest entries: {sum(1 for _ in m.splitlines())}.",
    "",
    "## 9. STOP",
    "",
    "Merging into main and the V0 scoping decision are human actions. No merge, no main push, no further corpus writes.",
]
(ROOT / "LANE14-REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(f"wrote LANE14-REPORT.md; manifest_sha256={manifest_hash}")
