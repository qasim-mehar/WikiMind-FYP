"""Read-only digest of stored claims/nodes/edges to source mini-check questions."""

from __future__ import annotations

import textwrap

from live import connect_live

with connect_live() as conn:
    print("=== PROJECTS ===")
    for r in conn.execute("SELECT id, name FROM projects ORDER BY name").fetchall():
        print(f"  {r['id']}  {r['name']}")

    print("\n=== DOCUMENTS ===")
    for r in conn.execute(
        "SELECT id, source_uri, sha256 FROM documents ORDER BY source_uri"
    ).fetchall():
        print(f"  {r['id']}  {r['source_uri']}")

    print("\n=== NODES BY TYPE ===")
    for r in conn.execute(
        "SELECT node_type, count(*) AS n FROM nodes GROUP BY node_type ORDER BY node_type"
    ).fetchall():
        print(f"  {r['node_type']}: {r['n']}")

    print("\n=== NODE TITLES/DESCRIPTIONS ===")
    for r in conn.execute(
        """
        SELECT n.node_type, n.slug, n.status, nv.rendered_text
        FROM nodes n
        LEFT JOIN node_versions nv
          ON nv.id = n.current_version_id AND nv.project_id = n.project_id
        ORDER BY n.node_type, n.slug
        """
    ).fetchall():
        txt = (r["rendered_text"] or "").replace("\n", " ")[:300]
        print(f"  [{r['node_type']}/{r['status']}] {r['slug']} :: {txt}")

    print("\n=== CLAIMS BY DOCUMENT (samples) ===")
    for r in conn.execute(
        """
        SELECT d.source_uri, count(*) AS n
        FROM claims c JOIN documents d ON d.id = c.document_id AND d.project_id = c.project_id
        GROUP BY d.source_uri ORDER BY d.source_uri
        """
    ).fetchall():
        print(f"  {r['source_uri']}: {r['n']} claims")

    print("\n=== ALL CLAIM TEXTS (grouped by document) ===")
    rows = conn.execute(
        """
        SELECT d.source_uri, c.text
        FROM claims c JOIN documents d ON d.id = c.document_id AND d.project_id = c.project_id
        ORDER BY d.source_uri, c.id
        """
    ).fetchall()
    cur = None
    for r in rows:
        if r["source_uri"] != cur:
            cur = r["source_uri"]
            print(f"\n  --- {cur} ---")
        wrapped = textwrap.shorten((r["text"] or "").replace("\n", " "), width=320)
        print(f"    - {wrapped}")

    print("\n=== CONTRADICTION EDGES ===")
    for r in conn.execute(
        """
        SELECT e.edge_type, a.slug AS src, b.slug AS dst,
               (SELECT string_agg(c.text, ' | ') FROM edge_sources es
                  JOIN claims c ON c.id = es.claim_id AND c.project_id = es.project_id
                 WHERE es.edge_id = e.id AND es.project_id = e.project_id) AS evidence
        FROM edges e
        JOIN nodes a ON a.id = e.src_node AND a.project_id = e.project_id
        JOIN nodes b ON b.id = e.dst_node AND b.project_id = e.project_id
        WHERE e.edge_type IN ('contradicts','supersedes')
        ORDER BY e.edge_type, a.slug, b.slug
        """
    ).fetchall():
        print(f"  [{r['edge_type']}] {r['src']}  <->  {r['dst']}")
        print(
            f"      evidence: {textwrap.shorten((r['evidence'] or '').replace(chr(10), ' '), width=500)}"
        )
