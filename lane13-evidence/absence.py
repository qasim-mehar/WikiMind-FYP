"""Read-only claim-absence evidence for the deliberately unanswerable questions."""

from __future__ import annotations

import json
from pathlib import Path

from live import connect_live

EV = Path(__file__).resolve().parent
CHECKS = {
    "Q28": ("inference latency", ["latency", "millisecond"]),
    "Q29": ("Adam weight decay coefficient", ["weight decay"]),
    "Q30": ("V100 GPUs for the GAN model", ["V100", "adversarial nets", "generative adversarial"]),
    "Q31": ("December 2019 Wikipedia dump", ["December 2019", "December 2018"]),
}

out = {}
with connect_live() as conn:
    total = conn.execute("SELECT count(*) AS n FROM claims").fetchone()["n"]
    for qid, (label, terms) in CHECKS.items():
        detail = {}
        for term in terms:
            rows = conn.execute(
                "SELECT text FROM claims WHERE text ILIKE %s ORDER BY text", (f"%{term}%",)
            ).fetchall()
            detail[term] = [r["text"] for r in rows]
        out[qid] = {"target": label, "corpus_claims_total": total, "term_hits": detail}

(EV / "absence.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
for qid, data in out.items():
    print(f"{qid} target={data['target']!r}")
    for term, hits in data["term_hits"].items():
        print(f"    {term!r}: {len(hits)} hit(s)")
        for h in hits[:2]:
            print(f"        - {h[:140]}")
