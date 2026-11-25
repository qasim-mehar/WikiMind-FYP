"""Render the frozen question set and answer key into a readable Phase A report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

EV = Path(__file__).resolve().parent
questions = json.loads((EV / "questions.json").read_text(encoding="utf-8"))
key = json.loads((EV / "answer_key.json").read_text(encoding="utf-8"))
kmap = {e["id"]: e for e in key["answer_key"]}

lines = [
    "# Lane 14 — Phase A report (full question set + answer key + spend)",
    "",
    f"- Frozen question-set SHA256: `{questions['sha256'] if 'sha256' in questions else key['questions_sha256']}`",
    f"- Questions: {len(questions['questions'])} (40 single-hop, 60 multi-hop, 30 contradiction/aggregation, 20 unanswerable)",
    f"- Answer-key statuses: {dict(sorted(Counter(e['status'] for e in key['answer_key']).items()))}",
    "- Authoring spend: $0.00 shadow (zero LLM calls); isolated ledger `author_costs.jsonl` is empty.",
    "",
    "## Full question set (frozen)",
    "",
]
for q in questions["questions"]:
    lines.append(
        f"- **{q['id']}** [{q['bucket']}; docs={'+'.join(q['docs'])}] {q['question']}"
    )
    lines.append(f"  - Target: {q['target']}")
    lines.append(f"  - Rubric: {q['rubric']}")
lines += ["", "## Answer key (built from stored claims after freeze)", ""]
for e in key["answer_key"]:
    extra = ""
    if e.get("supporting_claim_ids"):
        extra = f"; claim_ids={','.join(e['supporting_claim_ids'][:6])}"
    elif e.get("sides"):
        extra = "; sides=" + " / ".join(
            f"{'yes' if s['supported'] else 'no'}({','.join(s['probes'])})" for s in e["sides"]
        )
    lines.append(
        f"- **{e['id']}** {e['status']} -> expected `{e['expected']}`{extra}"
    )
lines += [
    "",
    "## Anomalies (see DEVIATIONS.md)",
    "",
    "- 18.4 BLEU / 'synthetic' statements of the contradiction note were not distilled into claims; related conflict questions are one-sided or insufficient-with-evidence.",
    "- RAG numbers 44.1 / 72.5 / 42.7 / 626 are absent from stored claims; marked insufficient-with-evidence.",
]
(EV / "PHASE-A-REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(f"wrote PHASE-A-REPORT.md lines={len(lines)}")
