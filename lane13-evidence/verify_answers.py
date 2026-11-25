"""Machine-verify Lane 13 answers against the stored corpus (Rulings 21/22/24/25).

Read-only.  Checks that every displayed short ref resolves to a stored claim,
that the displayed document slug matches the claim's source document, that every
bracket ref in the prose also appears in the verified citation list, and that
per-question shadow cost is parsed exactly from the CLI output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from live import connect_live

EV = Path(__file__).resolve().parent
REF = re.compile(r"c-[0-9a-f]{12}")
SHADOW = re.compile(r"shadow:\s*\$([\d.]+)")
CIT_LINE = re.compile(r"^\s*\[(c-[0-9a-f]{12})\]\s+(\S+)\s+—\s*(.*)$")

questions = {q["id"]: q for q in json.loads((EV / "questions.json").read_text(encoding="utf-8"))}

rows = []
with connect_live() as conn:
    # Cache every claim's short ref -> (full uuid, document slug, text).
    claim_rows = conn.execute(
        """
        SELECT c.id AS claim_id, c.text,
               regexp_replace(replace(c.id::text, '-', ''), '^(.{12}).*$', '\\1') AS hex12,
               d.source_uri
        FROM claims c
        JOIN documents d ON d.id = c.document_id AND d.project_id = c.project_id
        """
    ).fetchall()
    from wikimind.qa.pipeline import document_slug

    by_ref = {}
    for r in claim_rows:
        by_ref[f"c-{r['hex12']}"] = {
            "claim_id": str(r["claim_id"]),
            "slug": document_slug(r["source_uri"]),
            "text": r["text"],
        }

    for qid, q in questions.items():
        path = EV / "answers" / f"{qid}.txt"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        answer_match = re.search(r"^answer:\s+(.*)$", text, re.MULTILINE)
        sufficient_match = re.search(r"^sufficient:\s+(\w+)", text, re.MULTILINE)
        shadow_match = SHADOW.search(text)
        cit_lines = [CIT_LINE.match(line) for line in text.splitlines()]
        cit_lines = [m for m in cit_lines if m]
        cited_refs = [m.group(1) for m in cit_lines]
        prose = answer_match.group(1) if answer_match else ""
        prose_refs = REF.findall(prose)
        retrieval_match = re.search(r"^retrieval:\s+(.*)$", text, re.MULTILINE)

        problems = []
        for ref in set(cited_refs + prose_refs):
            if ref not in by_ref:
                problems.append(f"unresolved ref {ref}")
        for m in cit_lines:
            ref, slug = m.group(1), m.group(2)
            if ref in by_ref and by_ref[ref]["slug"] != slug:
                problems.append(f"slug mismatch {ref}: shown {slug}, stored {by_ref[ref]['slug']}")
        for ref in prose_refs:
            if ref not in cited_refs:
                problems.append(f"prose ref {ref} absent from verified list")
        if q["kind"] == "sufficient" and sufficient_match and sufficient_match.group(1) == "false":
            problems.append("marked insufficient though corpus-verifiable target exists")

        rows.append(
            {
                "id": qid,
                "kind": q["kind"],
                "sufficient": sufficient_match.group(1) if sufficient_match else None,
                "citations": len(cited_refs),
                "prose_refs": len(prose_refs),
                "verified": not problems,
                "problems": problems,
                "shadow_usd": float(shadow_match.group(1)) if shadow_match else None,
                "retrieval": retrieval_match.group(1) if retrieval_match else "",
                "answer": prose,
                "cited": [
                    {
                        "ref": r,
                        "claim_id": by_ref[r]["claim_id"],
                        "slug": by_ref[r]["slug"],
                        "claim": by_ref[r]["text"],
                    }
                    for r in cited_refs
                    if r in by_ref
                ],
            }
        )

(EV / "verification.json").write_text(
    json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
)
for row in rows:
    status = "OK" if row["verified"] else "FAIL"
    print(
        f"{row['id']:>4} {row['kind']:<11} {status:<4} "
        f"suff={row['sufficient']!s:<5} cites={row['citations']:<2} "
        f"shadow={row['shadow_usd']} {row['problems']}"
    )
print(f"\nTOTAL shadow = ${sum(r['shadow_usd'] or 0 for r in rows):.12f}")
