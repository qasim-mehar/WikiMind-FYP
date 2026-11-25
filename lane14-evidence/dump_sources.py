"""Phase A1: dump the four source documents' full text from the document store.

Reads ONLY projects / documents / channels. Never opens claims or edges.
The live session is forced read-only (see live.py). Output goes to a scratch
directory outside the repo; raw text is untrusted data, never prompt text.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from live import connect_live

OUT = Path(sys.argv[1])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with connect_live() as conn:
        project = conn.execute("SELECT id, name FROM projects").fetchone()
        rows = conn.execute(
            """
            SELECT d.id, d.source_uri, d.sha256,
                   string_agg(c.raw_text, E'\\n' ORDER BY c.ordinal, c.id) AS full_text,
                   count(c.id) AS n_channels
            FROM documents d
            JOIN channels c ON c.document_id = d.id
            WHERE d.project_id = %s
            GROUP BY d.id, d.source_uri, d.sha256
            ORDER BY d.source_uri
            """,
            (project["id"],),
        ).fetchall()
    manifest = {
        "project": {"id": str(project["id"]), "name": project["name"]},
        "documents": [],
    }
    for row in rows:
        slug = Path(row["source_uri"]).stem
        text = row["full_text"]
        target = OUT / f"{slug}.txt"
        target.write_text(text, encoding="utf-8", newline="\n")
        manifest["documents"].append(
            {
                "id": str(row["id"]),
                "slug": slug,
                "source_uri": row["source_uri"],
                "sha256": row["sha256"],
                "n_channels": int(row["n_channels"]),
                "chars": len(text),
                "text_file": str(target),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    (OUT / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    for doc in manifest["documents"]:
        print(f"{doc['slug']}: {doc['chars']} chars, {doc['n_channels']} channels")


if __name__ == "__main__":
    main()
