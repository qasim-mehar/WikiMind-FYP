"""Ruling 25 rubric scorer for Lane 14 (draft verdicts + human-readable digest).

Uses the frozen answer key's `expected` field:
  answer-present                  -> correct if the target is substantively in the
                                     answer, sufficient=true, citations verified
  honest-insufficient             -> correct if sufficient=false with no fabricated
                                     target (the absent value must not be asserted)
  cite-both-sides                 -> correct if both conflict-side values appear and
                                     are cited, citations verified
  cite-present-side-honest-note   -> correct if the stored side is cited and the
                                     absent side is not fabricated

Produces verdicts_draft.json for manual review and judging.txt for the digest.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

EV = Path(__file__).resolve().parent
rows = json.loads((EV / "verification.json").read_text(encoding="utf-8"))
key = {
    e["id"]: e
    for e in json.loads((EV / "answer_key.json").read_text(encoding="utf-8"))["answer_key"]
}
questions = {
    q["id"]: q for q in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]
}

STOP = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "are",
    "was",
    "were",
    "what",
    "which",
    "does",
    "did",
    "how",
    "many",
    "much",
    "report",
    "reported",
    "states",
    "stated",
    "according",
    "model",
    "paper",
    "corpus",
    "value",
    "values",
    "between",
    "absent",
    "not",
    "stored",
    "target",
    "expected",
    "question",
    "answer",
}


def target_tokens(target: str) -> list[str]:
    nums = re.findall(r"\d+(?:\.\d+)?", target)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", target.lower()) if w not in STOP]
    return nums + sorted(set(words), key=len, reverse=True)[:5]


DRAFT = []
digest = []
for r in rows:
    qid = r["id"]
    k = key[qid]
    expected = k["expected"]
    ans = (r["answer"] or "").lower()
    cited_text = " ".join((c["claim"] or "") for c in r["cited"]).lower()
    blob = f"{ans} {cited_text}"
    suff = r["sufficient"]
    if suff in ("true", "false"):
        suff = suff == "true"
    reasons: list[str] = []
    verdict = None

    if expected == "answer-present":
        toks = target_tokens(k["target"])
        hits = [t for t in toks if t.lower() in blob]
        need = 1 if any(re.match(r"\d", t) for t in toks) else max(1, min(2, len(toks)))
        if len(hits) < need:
            reasons.append(f"target tokens {toks} -> hits {hits} (need {need})")
        if suff is not True:
            reasons.append(f"sufficient={suff}, expected true")
        if not r["verified"]:
            reasons.append(f"machine checks failed: {r['problems']}")
        verdict = "PASS" if not reasons else "REVIEW"
    elif expected == "honest-insufficient":
        if suff is not False:
            reasons.append(f"sufficient={suff}, expected false")
        if not r["verified"]:
            reasons.append(f"machine checks failed: {r['problems']}")
        # A fabricated target would normally show as a positive claim with citations
        if suff is not False and r["citations"] > 0:
            reasons.append("asserted answer with citations despite absent target")
        verdict = "PASS" if not reasons else "REVIEW"
    elif expected == "cite-both-sides":
        found = [any(term.lower() in blob for term in side["probes"]) for side in k["sides"]]
        if not all(found):
            reasons.append(f"conflict sides not both present: {found}")
        if not r["verified"]:
            reasons.append(f"machine checks failed: {r['problems']}")
        verdict = "PASS" if not reasons else "REVIEW"
    elif expected == "cite-present-side-honest-note":
        supported = [s for s in k["sides"] if s["supported"]]
        found = any(term.lower() in blob for s in supported for term in s["probes"])
        if not found:
            reasons.append("stored side not present in answer")
        if not r["verified"]:
            reasons.append(f"machine checks failed: {r['problems']}")
        verdict = "PASS" if not reasons else "REVIEW"
    else:
        reasons.append(f"unknown expected={expected}")
        verdict = "REVIEW"

    DRAFT.append(
        {
            "id": qid,
            "bucket": r["bucket"],
            "expected": expected,
            "draft": verdict,
            "reasons": reasons,
            "sufficient": suff,
            "shadow_usd": r["shadow_usd"],
            "retrieval": r["retrieval"],
        }
    )
    digest.append(f"\n===== {qid} [{r['bucket']}] expected={expected} draft={verdict} =====")
    digest.append(f"KEY: {k['target']}")
    digest.append(f"A: {r['answer']}")
    digest.append(f"sufficient={suff} verified={r['verified']} retrieval={r['retrieval']}")
    for c in r["cited"]:
        digest.append(f"  C [{c['slug']}] {c['claim']}")
    for reason in reasons:
        digest.append(f"  !! {reason}")

(EV / "verdicts_draft.json").write_text(
    json.dumps(DRAFT, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
)
(EV / "judging.txt").write_text("\n".join(digest) + "\n", encoding="utf-8", newline="\n")
from collections import Counter

print(f"draft counts: {dict(Counter(d['draft'] for d in DRAFT))}")
print(f"by bucket: {dict(Counter((d['bucket'], d['draft']) for d in DRAFT))}")
