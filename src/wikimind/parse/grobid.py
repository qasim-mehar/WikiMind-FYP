"""Optional GROBID client and TEI XML parser.

Run GROBID with::

    docker run -d -p 8070:8070 lfoppiano/grobid:0.8.1

Then set ``WIKIMIND_GROBID_URL=http://localhost:8070``.  This module uses only
the standard-library XML parser and is not imported for offline parsers.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import httpx

from .errors import GrobidError, GrobidUnavailableError
from .models import ChannelBuilder, ParsedDocument, make_document
from .registry import register

_TEI_NS = "http://www.tei-c.org/ns/1.0"
_NS = "{" + _TEI_NS + "}"
_PROCESS_PATH = "/api/processFulltextDocument"
_HEALTH_PATH = "/api/health"
_TIMEOUT_SECONDS = 120.0


def grobid_base_url() -> str:
    url = os.environ.get("WIKIMIND_GROBID_URL", "").strip()
    if not url:
        raise GrobidUnavailableError(
            "WIKIMIND_GROBID_URL is not set; start GROBID and point this "
            "variable at it, e.g. http://localhost:8070"
        )
    return url.rstrip("/")


def _process_url(base: str) -> str:
    if base.endswith(_PROCESS_PATH):
        return base
    return base + _PROCESS_PATH


def _health_url(base: str) -> str:
    base = base.removesuffix(_PROCESS_PATH)
    return base + _HEALTH_PATH


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _find_title(root: ET.Element) -> str:
    title_stmt = root.find(f".//{_NS}titleStmt")
    if title_stmt is not None:
        title = title_stmt.find(f"{_NS}title")
        if title is not None:
            text = _text(title)
            if text:
                return text
    title = root.find(f".//{_NS}title")
    return _text(title)


def _find_abstract(root: ET.Element) -> str:
    abstract = root.find(f".//{_NS}abstract")
    if abstract is None:
        return ""
    paragraphs = abstract.findall(f".//{_NS}p")
    if paragraphs:
        texts = [_text(p) for p in paragraphs]
        joined = "\n\n".join(text for text in texts if text).strip()
        if joined:
            return joined
    return _text(abstract)


def _emit_figure(figure: ET.Element, builder: ChannelBuilder) -> None:
    kind = (
        "table"
        if (figure.get("type") or "").lower() == "table"
        else "figure_caption"
    )
    head = _text(figure.find(f"{_NS}head"))
    description = _text(figure.find(f"{_NS}figDesc"))
    if head and description:
        text = f"{head} {description}"
    elif head:
        text = head
    elif description:
        text = description
    else:
        text = _text(figure)
    if text:
        builder.add(kind, text)


def _walk_div(div: ET.Element, builder: ChannelBuilder) -> None:
    head = _text(div.find(f"{_NS}head"))
    paragraphs = [
        _text(p) for p in div.findall(f"{_NS}p") if _text(p)
    ]
    body = "\n\n".join(paragraphs)
    if head and body:
        builder.add("section", f"{head}\n\n{body}")
    elif head:
        builder.add("section", head)
    elif body:
        builder.add("section", body)

    for child in list(div):
        if child.tag == f"{_NS}figure":
            _emit_figure(child, builder)
        elif child.tag == f"{_NS}table":
            table_text = _text(child)
            if table_text:
                builder.add("table", table_text)
        elif child.tag == f"{_NS}div":
            _walk_div(child, builder)


def _emit_body(root: ET.Element, builder: ChannelBuilder) -> None:
    body = root.find(f".//{_NS}body")
    if body is None:
        return

    direct_paragraphs = [
        _text(p) for p in body.findall(f"{_NS}p") if _text(p)
    ]
    if direct_paragraphs:
        builder.add("section", "\n\n".join(direct_paragraphs))

    for child in list(body):
        if child.tag == f"{_NS}div":
            _walk_div(child, builder)
        elif child.tag == f"{_NS}figure":
            _emit_figure(child, builder)
        elif child.tag == f"{_NS}table":
            table_text = _text(child)
            if table_text:
                builder.add("table", table_text)


def _emit_references(root: ET.Element, builder: ChannelBuilder) -> None:
    back = root.find(f".//{_NS}back")
    if back is None:
        return

    references = back.findall(f".//{_NS}biblStruct")
    if references:
        for reference in references:
            text = _text(reference)
            if text:
                builder.add("reference", text)
        return

    for list_bibl in back.findall(f".//{_NS}listBibl"):
        text = _text(list_bibl)
        if text:
            builder.add("reference", text)


def parse_tei(
    tei_xml: bytes, *, data: bytes, source_filename: str
) -> ParsedDocument:
    """Parse GROBID TEI XML into the ParsedDocument contract."""

    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError as exc:
        raise GrobidError(f"GROBID returned invalid TEI XML: {exc}") from exc

    if not root.tag.endswith("TEI"):
        raise GrobidError(f"GROBID returned unexpected root element {root.tag!r}")

    builder = ChannelBuilder()
    title = _find_title(root)
    if title:
        builder.add("title", title)
    abstract = _find_abstract(root)
    if abstract:
        builder.add("abstract", abstract)
    _emit_body(root, builder)
    _emit_references(root, builder)

    return make_document(
        "grobid",
        source_filename,
        data,
        builder.finish(),
        page_count=None,
    )


@register("grobid")
def parse_grobid(data: bytes, source_filename: str) -> ParsedDocument:
    base = grobid_base_url()
    url = _process_url(base)
    try:
        response = httpx.post(
            url,
            files={"input": (source_filename, data, "application/pdf")},
            headers={"Accept": "application/xml"},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise GrobidError(f"GROBID request failed: {exc}") from exc

    if response.status_code != 200:
        detail = response.text[:300]
        raise GrobidError(
            f"GROBID returned HTTP {response.status_code}: {detail}"
        )
    return parse_tei(
        response.content,
        data=data,
        source_filename=source_filename,
    )


def probe_grobid(base_url: str | None = None) -> tuple[bool, str]:
    """Probe the GROBID health endpoint; never raises for network failures."""

    try:
        base = (base_url or grobid_base_url()).rstrip("/")
    except GrobidUnavailableError as exc:
        return False, str(exc)

    url = _health_url(base)
    try:
        response = httpx.get(url, timeout=5.0)
    except httpx.HTTPError as exc:
        return False, f"could not reach {url}: {exc}"
    if response.status_code == 200:
        return True, f"GROBID is reachable at {url}"
    return False, f"GROBID returned HTTP {response.status_code} from {url}"
