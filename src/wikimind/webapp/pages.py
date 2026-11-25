"""Server-rendered HTML for the three prototype pages.

No template engine and no build toolchain: small functions that escape every
value coming from the database or the model. The only JavaScript does file
reading and polling for compile progress.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable

from ..llm.cost import format_usd
from .models import (
    AskView,
    ClaimDetail,
    DocumentSummary,
    JobProgress,
    SelectableFile,
)

_CITATION_TOKEN = re.compile(r"\[(c-[0-9a-fA-F]+)\]")

_CSS = """
:root { color-scheme: light; --ink:#1c2430; --muted:#5d6b7a; --line:#dce3ea;
        --accent:#1f5d8f; --warn:#8a5a00; --bad:#a32626; --ok:#1b6b3a; }
* { box-sizing: border-box; }
body { margin:0; font:15px/1.55 system-ui, -apple-system, Segoe UI, sans-serif;
       color:var(--ink); background:#f6f8fa; }
header { background:#fff; border-bottom:1px solid var(--line); padding:14px 24px; }
header nav { max-width:960px; margin:0 auto; display:flex; gap:18px; align-items:baseline; }
header .brand { font-weight:700; color:var(--ink); text-decoration:none; font-size:17px; }
header a { color:var(--accent); text-decoration:none; }
main { max-width:960px; margin:24px auto; padding:0 24px 48px; }
h1 { font-size:24px; margin:0 0 6px; } h2 { font-size:18px; margin:28px 0 10px; }
p { margin:8px 0; } .muted { color:var(--muted); }
.card { background:#fff; border:1px solid var(--line); border-radius:8px;
        padding:16px 18px; margin:12px 0; }
table { width:100%; border-collapse:collapse; background:#fff; }
th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); }
th { color:var(--muted); font-size:13px; }
a { color:var(--accent); }
code { background:#eef2f6; padding:1px 5px; border-radius:4px; font-size:13px; }
label { display:block; margin:14px 0 5px; font-weight:600; }
input[type=text], textarea, select { width:100%; padding:9px 11px; border:1px solid var(--line);
       border-radius:6px; font:inherit; background:#fff; }
button { margin-top:14px; padding:9px 16px; background:var(--accent); color:#fff;
         border:0; border-radius:6px; cursor:pointer; font:inherit; }
.notice { border-left:4px solid var(--warn); background:#fff8e6; padding:10px 14px; margin:12px 0; }
.error { border-left:4px solid var(--bad); background:#fff0f0; padding:10px 14px; margin:12px 0; }
.ok { border-left:4px solid var(--ok); background:#eef8f1; padding:10px 14px; margin:12px 0; }
.cite { border-left:3px solid var(--line); padding:6px 12px; margin:8px 0; }
.badge { font-size:12px; color:var(--muted); border:1px solid var(--line);
         border-radius:99px; padding:1px 8px; }
ol.stages { margin:8px 0; padding-left:22px; } ol.stages li { padding:2px 0; }
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def layout(title: str, body: str, *, project: str = "") -> str:
    suffix = f" · project {_e(project)}" if project else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} · WikiMind local interface</title><style>{_CSS}</style></head>
<body><header><nav>
<a class="brand" href="/">WikiMind</a>
<a href="/documents">Documents</a><a href="/add">Add</a><a href="/ask">Ask</a>
<span class="muted">{suffix}</span>
<span class="muted" style="margin-left:auto">local dev tool · no auth</span>
</nav></header><main>{body}</main></body></html>"""


def index_page(project: str, count: int) -> str:
    return layout(
        "Home",
        f"""<h1>Local interface prototype</h1>
<p class="muted">A browser view over a sandbox compiled wiki. No auth, loopback only.
Real model calls are metered to the interface's own ledger.</p>
<div class="card"><p><strong>Project:</strong> {_e(project)}</p>
<p><strong>Documents in corpus:</strong> {count}</p></div>
<p><a href="/documents">Browse documents</a> · <a href="/add">Add a source</a> ·
<a href="/ask">Ask a question</a></p>""",
        project=project,
    )


def documents_page(docs: Iterable[DocumentSummary], project: str) -> str:
    rows = "".join(
        f"<tr><td><strong>{_e(doc.title)}</strong><br><span class='muted'>{_e(doc.slug)}</span></td>"
        f"<td>{doc.claim_count}</td><td><code>{_e(doc.sha256[:12])}</code></td>"
        f"<td>{_e(doc.parser)}</td><td class='muted'>{_e(doc.source_uri)}</td></tr>"
        for doc in docs
    )
    body = f"""<h1>Documents</h1>
<p class="muted">The sandbox corpus for project {_e(project)}. Claim counts come from the
compiled graph; source text is never shown here.</p>
<table><thead><tr><th>Title / slug</th><th>Claims</th><th>SHA-256</th><th>Parser</th>
<th>Source</th></tr></thead><tbody>{
        rows or "<tr><td colspan='5' class='muted'>No documents yet — add one.</td></tr>"
    }</tbody></table>
<p><a href="/add">Add a source document</a></p>"""
    return layout("Documents", body, project=project)


def add_page(files: Iterable[SelectableFile], *, project: str, message: str = "") -> str:
    options = "".join(
        f"<option value='{_e(file.path)}'>{_e(file.name)} ({file.size} bytes)</option>"
        for file in files
    )
    select = (
        f"<label for='existing'>Or select a local file</label>"
        f"<select id='existing'><option value=''>— none —</option>{options}</select>"
        if options
        else "<p class='muted'>No local files in the upload directory yet.</p>"
    )
    notice = f"<div class='notice'>{_e(message)}</div>" if message else ""
    body = f"""<h1>Add a document</h1>
{notice}
<p class="muted">Uploading writes an immutable copy into the sandbox upload directory,
then runs the existing compile pipeline. Stage progress appears on the next page.</p>
<div class="card">
<label for="file">Upload a source (.txt, .md, .pdf)</label>
<input id="file" type="file" accept=".txt,.md,.markdown,.pdf">
{select}
<button id="submit" type="button">Add &amp; compile</button>
<p id="error" class="error" hidden></p>
</div>
<script>
async function startAdd() {{
  const error = document.getElementById('error');
  error.hidden = true;
  const selected = document.getElementById('existing').value;
  const file = document.getElementById('file').files[0];
  let payload;
  if (selected) {{
    payload = {{ path: selected }};
  }} else if (file) {{
    const dataUrl = await new Promise((resolve, reject) => {{
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    }});
    payload = {{ filename: file.name, content_base64: dataUrl.split(',')[1] || '' }};
  }} else {{
    error.textContent = 'Choose a file or select a local one.';
    error.hidden = false;
    return;
  }}
  try {{
    const response = await fetch('/add', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(payload)
    }});
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json();
    window.location.href = '/add/' + data.job_id;
  }} catch (err) {{
    error.textContent = String(err);
    error.hidden = false;
  }}
}}
document.getElementById('submit').addEventListener('click', startAdd);
</script>"""
    return layout("Add a document", body, project=project)


def stage_items(progress: JobProgress) -> str:
    if not progress.stages:
        return "<li class='muted'>Waiting for the first stage…</li>"
    return "".join(f"<li>{_e(stage)}</li>" for stage in progress.stages)


def job_page(progress: JobProgress, *, project: str) -> str:
    state = {"running": "Running", "done": "Done", "failed": "Failed"}.get(
        progress.state, progress.state
    )
    detail = ""
    if progress.state == "done":
        detail = (
            f"<div class='ok'>{_e(progress.message)}<br>"
            f"Shadow cost: {format_usd(progress.shadow_cost_usd)} · "
            f"{progress.claims_committed} claim(s) committed</div>"
            "<p><a href='/documents'>See the documents page</a> · "
            "<a href='/ask'>Ask a question</a></p>"
        )
    elif progress.state == "failed":
        detail = f"<div class='error'>Compile failed: {_e(progress.message)}</div>"
    body = f"""<h1>Compile progress</h1>
<p>Job <code>{_e(progress.id)}</code> · <strong id="state">{_e(state)}</strong></p>
<ol class="stages" id="stages">{stage_items(progress)}</ol>
<div id="detail">{detail}</div>
<p><a href="/add">Back to Add</a></p>
<script>
async function poll() {{
  const response = await fetch('/add/{_e(progress.id)}/progress');
  if (!response.ok) return;
  const data = await response.json();
  document.getElementById('state').textContent = data.state;
  document.getElementById('stages').innerHTML = data.stages.map(
    s => '<li>' + s.replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</li>').join('');
  if (data.state !== 'running') {{
    const d = document.getElementById('detail');
    if (data.state === 'failed') {{
      d.innerHTML = '<div class="error">Compile failed: ' +
        data.message.replace(/&/g,'&amp;').replace(/</g,'&lt;') + '</div>';
    }} else {{
      d.innerHTML = '<div class="ok">' + data.message.replace(/&/g,'&amp;').replace(/</g,'&lt;') +
        '<br>Shadow cost: ' + data.shadow_cost_usd.toFixed(6) + ' USD · ' +
        data.claims_committed + ' claim(s) committed</div>';
    }}
    return;
  }}
  setTimeout(poll, 700);
}}
if ('{_e(progress.state)}' === 'running') setTimeout(poll, 700);
</script>"""
    return layout("Compile progress", body, project=project)


def progress_json(progress: JobProgress) -> dict[str, object]:
    """The polling payload; stages are plain text, never trusted HTML."""

    return {
        "id": progress.id,
        "state": progress.state,
        "stages": list(progress.stages),
        "shadow_cost_usd": progress.shadow_cost_usd,
        "claims_committed": progress.claims_committed,
        "message": progress.message,
    }


def ask_page(project: str, *, question: str = "", result: str = "") -> str:
    body = f"""<h1>Ask</h1>
<p class="muted">Answers come only from claims in the compiled graph. Every citation
links to the underlying claim and its source document.</p>
<form id="ask-form">
<label for="question">Question</label>
<textarea id="question" name="question" rows="3" placeholder="What does the corpus say about…?">{_e(question)}</textarea>
<button type="submit">Ask</button>
</form>
<div id="result">{result}</div>
<script>
document.getElementById('ask-form').addEventListener('submit', async function (event) {{
  event.preventDefault();
  const result = document.getElementById('result');
  result.innerHTML = '<p class="muted">Retrieving claims and composing an answer…</p>';
  try {{
    const response = await fetch('/ask', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ question: document.getElementById('question').value }})
    }});
    result.innerHTML = await response.text();
  }} catch (err) {{
    result.innerHTML = '<div class="error">' + String(err) + '</div>';
  }}
}});
</script>"""
    return layout("Ask", body, project=project)


def _linked_answer(view: AskView) -> str:
    by_ref = {citation.ref.lower(): citation.claim_id for citation in view.citations}
    escaped = _e(view.answer)

    def replace(match: re.Match[str]) -> str:
        target = by_ref.get(match.group(1).lower())
        if target is None:
            return match.group(0)
        return f'<a href="/claims/{_e(target)}">{_e(match.group(0))}</a>'

    return _CITATION_TOKEN.sub(replace, escaped).replace("\n", "<br>")


def ask_result_html(view: AskView) -> str:
    sufficiency = (
        "<div class='ok'>The compiled wiki contained enough evidence for this answer.</div>"
        if view.sufficient
        else "<div class='notice'><strong>Evidence insufficient.</strong> "
        "The compiled wiki does not contain enough claims to answer this fully. "
        "The relevant claims are shown below; nothing was invented to close the gap.</div>"
    )
    cards = []
    for citation in view.citations:
        cards.append(
            f"<div class='cite'><a href='/claims/{_e(citation.claim_id)}'>"
            f"[{_e(citation.ref)}]</a> "
            f"<span class='badge'>{_e(citation.status)}</span><br>"
            f"<span>{_e(citation.text) or '<em class=muted>claim text unavailable</em>'}</span><br>"
            f"<span class='muted'>source: {_e(citation.document_slug)} — "
            f"{_e(citation.source_uri)}</span></div>"
        )
    citations_html = "".join(cards) or "<p class='muted'>No verified citations.</p>"
    diagnostics = (
        f"retrieved {view.retrieved_claims} claim(s)"
        + (" (capped)" if view.truncated else "")
        + f" · shadow {format_usd(view.shadow_cost_usd)}"
    )
    if view.unverified_removed:
        diagnostics += f" · {view.unverified_removed} unverified citation(s) removed"
    if view.retrieval_note:
        diagnostics += f" · {view.retrieval_note}"
    return f"""{sufficiency}
<h2>Answer</h2><div class="card">{_linked_answer(view)}</div>
<h2>Citations</h2>{citations_html}
<p class="muted">{_e(diagnostics)}</p>"""


def ask_result_page(view: AskView, *, project: str) -> str:
    body = f"<p><a href='/ask'>← Ask another question</a></p><h1>Answer</h1>{ask_result_html(view)}"
    return layout("Answer", body, project=project)


def claim_page(detail: ClaimDetail, *, project: str) -> str:
    body = f"""<h1>Claim {_e(detail.ref)}</h1>
<div class="card"><p>{_e(detail.text)}</p>
<p class="muted">Anchor quote: <em>{_e(detail.anchor_quote) or "—"}</em></p>
<p>Status: <span class="badge">{_e(detail.status)}</span></p></div>
<h2>Source document</h2>
<div class="card"><p><strong>{_e(detail.document_title)}</strong> ({_e(detail.document_slug)})</p>
<p class="muted">{_e(detail.source_uri)}</p></div>
<p><code>{_e(detail.claim_id)}</code></p>
<p><a href="/ask">Back to Ask</a></p>"""
    return layout("Claim detail", body, project=project)


def error_page(message: str, *, project: str = "") -> str:
    return layout(
        "Unavailable",
        f"<h1>Unavailable</h1><div class='error'>{_e(message)}</div>",
        project=project,
    )
