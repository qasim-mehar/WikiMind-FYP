"""Machine verification of Lane 14 answers (Rulings 21/22/24/25, M3a).

Read-only. For every final answer, check:
  - every displayed short ref resolves to a stored claim id;
  - the displayed document slug matches the claim's source document;
  - every bracket ref in the prose also appears in the verified citation list;
  - the claim's anchor_quote exists in its channel raw text (exact or
    whitespace-normalized) -- the deterministic M3a anchor-validity check.

Also records sufficiency, retrieval line, per-answer shadow and answer text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from live import connect_live

from wikimind.qa.pipeline import document_slug

EV = Path(__file__).resolve().parent
REF = re.compile(r"c-[0-9a-f]{12}")
SHADOW = re.compile(r"shadow:\s*\$([\d.]+)")
CIT_LINE = re.compile(r"^\s*\[(c-[0-9a-f]{12})\]\s+(\S+)\s+\u2014\s*(.*)$")

questions = {
    q["id"]: q for q in json.loads((EV / "questions.json").read_text(encoding="utf-8"))["questions"]
}
key = {
    e["id"]: e
    for e in json.loads((EV / "answer_key.json").read_text(encoding="utf-8"))["answer_key"]
}
run_manifest = {
    r["id"]: r for r in json.loads((EV / "run_manifest.json").read_text(encoding="utf-8"))
}

rows = []
with connect_live() as conn:
    assert (
        conn.execute("SHOW default_transaction_read_only").fetchone()[
            "default_transaction_read_only"
        ]
        == "on"
    )
    claim_rows = conn.execute(
        """
        SELECT c.id AS claim_id, c.text, c.anchor_quote, ch.raw_text, d.source_uri,
               regexp_replace(replace(c.id::text, '-', ''), '^(.{12}).*$', '\\1') AS hex12
        FROM claims c
        JOIN documents d ON d.id = c.document_id AND d.project_id = c.project_id
        LEFT JOIN channels ch ON ch.id = c.channel_id AND ch.document_id = c.document_id
        """
    ).fetchall()
    by_ref: dict[str, dict] = {}
    for r in claim_rows:
        a = r["anchor_quote"] or ""
        t = r["raw_text"] or ""
        anchored = bool(a) and (a in t or " ".join(a.split()) in " ".join(t.split()))
        by_ref[f"c-{r['hex12']}"] = {
            "claim_id": str(r["claim_id"]),
            "slug": document_slug(r["source_uri"]),
            "text": r["text"],
            "anchor_quote": a,
            "anchored": anchored,
        }

    for qid, q in questions.items():
        path = EV / "answers" / f"{qid}.txt"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        answer_match = re.search(r"^answer:\s+(.*)$", text, re.MULTILINE)
        sufficient_match = re.search(r"^sufficient:\s+(\w+)", text, re.MULTILINE)
        shadow_match = SHADOW.search(text)
        cit_lines = [m for m in (CIT_LINE.match(line) for line in text.splitlines()) if m]
        cited_refs = [m.group(1) for m in cit_lines]
        prose = answer_match.group(1) if answer_match else ""
        prose_refs = REF.findall(prose)
        retrieval_match = re.search(r"^retrieval:\s+(.*)$", text, re.MULTILINE)

        problems: list[str] = []
        provider_failure = run_manifest.get(qid, {}).get("exit") != 0 or answer_match is None
        if provider_failure:
            problems.append("provider failure: no successful answer (503 after 6 attempts)")

        for ref in set(cited_refs + prose_refs):
            if ref not in by_ref:
                problems.append(f"unresolved ref {ref}")
        for m in cit_lines:
            ref, slug = m.group(1), m.group(2)
            if ref in by_ref and by_ref[ref]["slug"] != slug:
                problems.append(f"slug mismatch {ref}: shown {slug}, stored {by_ref[ref]['slug']}")
            if ref in by_ref and not by_ref[ref]["anchored"]:
                problems.append(f"anchor invalid {ref}")
        for ref in prose_refs:
            if ref not in cited_refs:
                problems.append(f"prose ref {ref} absent from verified list")

        cited = [
            {
                "ref": r,
                "claim_id": by_ref[r]["claim_id"],
                "slug": by_ref[r]["slug"],
                "claim": by_ref[r]["text"],
                "anchor_quote": by_ref[r]["anchor_quote"],
                "anchored": by_ref[r]["anchored"],
            }
            for r in cited_refs
            if r in by_ref
        ]
        rows.append(
            {
                "id": qid,
                "bucket": q["bucket"],
                "expected": key[qid]["expected"],
                "status": key[qid]["status"],
                "sufficient": sufficient_match.group(1) if sufficient_match else None,
                "citations": len(cited_refs),
                "prose_refs": len(prose_refs),
                "verified": not problems,
                "problems": problems,
                "shadow_usd": float(shadow_match.group(1)) if shadow_match else None,
                "retrieval": retrieval_match.group(1) if retrieval_match else "",
                "answer": prose,
                "cited": cited,
            }
        )

(EV / "verification.json").write_text(
    json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
)
ok = sum(1 for r in rows if r["verified"])
print(f"answers={len(rows)} verified={ok} rate={ok / len(rows):.4f}")
print(f"TOTAL shadow = ${sum(r['shadow_usd'] or 0 for r in rows):.12f}")
