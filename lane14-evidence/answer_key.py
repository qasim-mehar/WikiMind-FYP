"""Phase A3: build the Lane 14 answer key from stored claims (after freeze).

This is the FIRST time claims are consulted. Questions were authored and
frozen from the four source documents only (questions.json sha256 recorded in
DEVIATIONS.md). For each frozen question we classify its target as present /
absent / conflict against stored claims and record which claim ids support it.

Where a golden question's target is absent from stored claims, the expected
outcome is honest insufficiency-under-evidence (Ruling 25(2)/(3)) rather than
a fabricated answer. No frozen question text is edited.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from live import connect_live

EV = Path(__file__).resolve().parent

STOP = {
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "what",
    "which", "does", "did", "how", "many", "much", "report", "reported", "states",
    "stated", "according", "model", "paper", "corpus", "value", "values", "between",
}

# Deliberately-unanswerable group: ALL listed probes must be absent, scoped to
# the question's own documents. A probe is only a hit if every term in it is
# present in the same claim (avoids false positives from other papers' facts).
ABSENCE = {
    "U001": [["latency"]],
    "U002": [["weight decay"]],
    "U003": [["v100"]],
    "U004": [["december 2019"], ["2019 wikipedia"]],
    "U005": [["learning rate"]],
    "U006": [["25,000"], ["25000"]],
    "U007": [["adam"], ["sgd"]],
    "U008": [["mixed precision"], ["fp16"]],
    "U009": [["parsing", "development set"], ["parsing", "dev set"], ["dev f1"]],
    "U010": [["fever"]],
    "U011": [["number of articles"], ["wikipedia articles"]],
    "U012": [["temperature"]],
    "U013": [["squeeze"], ["excitation"]],
    "U014": [["dropout"]],
    "U015": [["author"]],
    "U016": [["bart-large", "bleu"], ["bart large", "bleu"], ["bart", "wmt 2014"]],
    "U017": [["submitted"], ["submission date"]],
    "U018": [["heads"]],
    "U019": [["learning rate"]],
    "U020": [["big bird"]],
}

# Conflict questions: each side is a list of alternative probe strings; a side
# is supported if ANY alternative has a claim hit.
# Targets too short for automatic tokenization; use explicit rubric probes.
PROBE_OVERRIDE = {
    "S021": [["markov chain"]],
}

CONFLICT = {
    "C001": [["41.8"], ["41.0"]],
    "C002": [["41.8"], ["41.0"]],
    "C003": [["abstract"], ["41.8"]],
    "C004": [["41.0"], ["41.8"]],
    "C011": [["28.4"], ["18.4"]],
    "C012": [["18.4"], ["synthetic"]],
    "C013": [["28.4"], ["over 2 bleu", "2 bleu"]],
    "C014": [["18.4"], ["10 bleu"]],
    "C016": [["over 2 bleu", "2 bleu"], ["did not improve", "no improvement"]],
    "C019": [["synthetic"], ["attention is all you need"]],
    "C021": [["shuohang wang"], ["murray campbell", "bowen zhou"]],
    "C022": [["shuohang wang"], ["jing jiang"]],
    "C023": [["shuohang wang"], ["murray campbell"]],
    "C024": [["gerry tesauro", "gerald tesauro"], ["murray campbell"]],
    "C030": [["over 2 bleu", "2 bleu"], ["10 bleu", "below"]],
}


def probes_for(target: str) -> list[str]:
    nums = re.findall(r"\d+(?:\.\d+)?", target)
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", target.lower())
    words = [w for w in words if w not in STOP]
    words.sort(key=len, reverse=True)
    out = nums + words[:6]
    seen, uniq = set(), []
    for p in out:
        if p.lower() not in seen:
            seen.add(p.lower())
            uniq.append(p)
    return uniq


def main() -> None:
    payload = json.loads((EV / "questions.json").read_text(encoding="utf-8"))
    questions = payload["questions"]
    path = EV / "questions.json"
    frozen = hashlib.sha256(path.read_bytes()).hexdigest()
    assert frozen == "bc0fd07c0148bc2452b1bca01aa946e093283967c47b116112b20d97a9648dc4"

    with connect_live() as conn:
        assert conn.execute("SHOW default_transaction_read_only").fetchone()[
            "default_transaction_read_only"
        ] == "on"
        rows = conn.execute(
            """
            SELECT c.id::text AS claim_id, c.text, c.anchor_quote,
                   d.source_uri, n.slug AS node_slug
            FROM claims c
            JOIN documents d ON d.id = c.document_id
            LEFT JOIN nodes n ON n.id = c.citation_node_id
            """
        ).fetchall()
    corpus = []
    for r in rows:
        blob = f"{r['text'] or ''} {r['anchor_quote'] or ''}".lower()
        corpus.append(
            {
                "claim_id": r["claim_id"],
                "slug": Path(r["source_uri"]).stem,
                "node_slug": r["node_slug"],
                "text": r["text"] or "",
                "anchor": r["anchor_quote"] or "",
                "blob": blob,
            }
        )

    def find_all(terms: list[str], docs: list[str] | None = None) -> list[str]:
        """Claim ids whose text contains every term (optionally doc-scoped)."""
        out = []
        for c in corpus:
            if docs and c["slug"] not in docs:
                continue
            if all(t.lower() in c["blob"] for t in terms):
                out.append(c["claim_id"])
        return out

    def find_any(terms: list[str]) -> list[str]:
        return sorted({cid for t in terms for cid in find_all([t])})

    key = []
    for q in questions:
        qid = q["id"]
        bucket = q["bucket"]
        entry = {
            "id": qid,
            "bucket": bucket,
            "docs": q["docs"],
            "question": q["question"],
            "target": q["target"],
            "rubric": q["rubric"],
        }
        if bucket == "unanswerable":
            hits = []
            for probe in ABSENCE[qid]:
                ids = find_all(probe, q["docs"])
                if ids:
                    hits.append({"terms": probe, "claim_ids": ids[:5]})
            entry["status"] = "absent" if not hits else "present-unexpected"
            entry["absence_probes"] = ABSENCE[qid]
            entry["unexpected_hits"] = hits
            entry["expected"] = (
                "honest-insufficient" if not hits else "review-needed"
            )
        elif qid in CONFLICT:
            sides = []
            for side in CONFLICT[qid]:
                ids = find_any(side)
                sides.append({"probes": side, "supported": bool(ids), "claim_ids": ids[:12]})
            supported = [s["supported"] for s in sides]
            if all(supported):
                entry["status"] = "conflict"
                entry["expected"] = "cite-both-sides"
            elif any(supported):
                entry["status"] = "one-sided"
                entry["expected"] = "cite-present-side-honest-note"
            else:
                entry["status"] = "insufficient-evidence"
                entry["expected"] = "honest-insufficient"
            entry["sides"] = sides
        else:
            override = PROBE_OVERRIDE.get(qid)
            if override:
                probes = override
                hits = [(p, find_all(p)) for p in probes]
            else:
                probes = probes_for(q["target"])
                hits = [(p, find_all([p])) for p in probes]
            any_hit = [(p, ids) for p, ids in hits if ids]
            claim_ids = sorted({cid for _, ids in any_hit for cid in ids})
            entry["probes"] = probes
            entry["supporting_claim_ids"] = claim_ids[:12]
            entry["supporting_documents"] = sorted(
                {c["slug"] for c in corpus if c["claim_id"] in claim_ids}
            )
            if any_hit:
                entry["status"] = "present"
                entry["expected"] = "answer-present"
            else:
                entry["status"] = "insufficient-evidence"
                entry["expected"] = "honest-insufficient"
        key.append(entry)

    out = {
        "questions_sha256": frozen,
        "claims_consulted": len(corpus),
        "legend": {
            "present": "target found in stored claims -> answer-present",
            "absent": "target absent from stored claims -> honest-insufficient",
            "conflict": "both sides found -> cite-both-sides",
            "one-sided": "one conflict side absent -> cite present side + honest note",
            "insufficient-evidence": "target absent -> honest-insufficient (Ruling 25(2)/(3))",
            "present-unexpected": "U probe found in its own documents -> review needed",
        },
        "answer_key": key,
    }
    path = EV / "answer_key.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    counts: dict[str, int] = {}
    for e in key:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    main()
