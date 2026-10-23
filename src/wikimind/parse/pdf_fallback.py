"""Offline, deterministic PDF parser built on pypdf plus heuristics.

No network, no models, no randomness.  The same PDF bytes and the same parser
implementation must produce the same channels and IDs.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader

from .errors import ParseError
from .models import ChannelBuilder, ParsedDocument, make_document
from .registry import register

_FOOTNOTE_PREFIXES = ("*", "∗", "†", "‡", "⋆", "§", "¶", "#")
_BOILERPLATE = (
    "permission",
    "attribution",
    "copyright",
    "all rights reserved",
    "arxiv:",
    "preprint",
    "under review",
    "published as",
    "licensed under",
)
_ABSTRACT_STOP_PREFIXES = (
    "keywords",
    "key words",
    "index terms",
    "acm classification",
    "ccs concepts",
    "additional key words",
)
_KNOWN_HEADINGS = {
    "abstract",
    "introduction",
    "background",
    "related work",
    "related works",
    "method",
    "methods",
    "methodology",
    "approach",
    "experiments",
    "experimental setup",
    "results",
    "evaluation",
    "discussion",
    "conclusion",
    "conclusions",
    "limitations",
    "future work",
    "references",
    "bibliography",
    "works cited",
    "literature cited",
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
    "appendix",
    "appendices",
    "supplementary material",
    "supplementary materials",
    "ethics statement",
    "broader impact",
    "reproducibility statement",
    "author contributions",
    "funding",
    "conflict of interest",
    "data availability",
}
_REFERENCE_HEADINGS = {
    "references",
    "bibliography",
    "works cited",
    "literature cited",
}
_LOWER_CONNECTORS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "via",
    "with",
}
_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.?|table)\s+(?:[A-Z]?\d+[a-z]?|[IVX]+)\s*[:.\-—|]",
    re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^(?P<number>\d{1,2}(?:\.\d{1,3}){0,3})\.?\s+(?P<rest>\S.*)$"
)
_REFERENCE_ENTRY_RE = re.compile(r"^(?:\[\d+\]|\d{1,3}\.)\s+")
_NUMBERING_PREFIX_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)\.?\s+"
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]*")
_TITLE_BAD_PUNCT = ".,;:"


@dataclass(frozen=True)
class _Line:
    page: int
    text: str


@dataclass(frozen=True)
class _Page:
    number: int
    text: str


def _is_footnote_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith(_FOOTNOTE_PREFIXES):
        return True
    lowered = stripped.lower()
    return lowered.startswith(
        (
            "equal contribution",
            "work performed",
            "corresponding author",
            "these authors",
            "authors contributed",
            "now at",
            "present address",
            "email:",
        )
    )


def _is_boilerplate(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _BOILERPLATE)


def _is_known_heading(text: str) -> bool:
    base = _NUMBERING_PREFIX_RE.sub("", text.strip()).rstrip(".:").strip().lower()
    return base in _KNOWN_HEADINGS


def _looks_like_heading_rest(text: str) -> bool:
    words = text.split()
    if not words or len(words) > 12:
        return False
    if not text[0].isupper():
        return False
    if text.rstrip().endswith(tuple(_TITLE_BAD_PUNCT)):
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in ("@", "http", "www.", "doi:", "arxiv:")):
        return False
    if any(char in text for char in ("=", "%", "$", "|", ":")):
        return False
    return " et al" not in lowered


def _looks_like_appendix_heading(text: str) -> bool:
    stripped = text.strip()
    match = re.match(r"^[A-Z]\.?\s+(?P<rest>\S.*)$", stripped)
    if not match:
        return False
    rest = match.group("rest").strip()
    words = rest.split()
    if not words or len(words) > 10 or len(stripped) > 90:
        return False
    if not rest[0].isupper():
        return False
    if any(char in rest for char in ".,;:?!@=%$()|"):
        return False
    if any(char.isdigit() for char in rest):
        return False
    if rest.isupper():
        return False
    lowered = rest.lower()
    if lowered.startswith(
        ("figure", "fig.", "table", "algorithm", "listing", "equation", "eq.")
    ):
        return False
    if " et al" in lowered:
        return False
    for word in _WORD_RE.findall(rest):
        if word.lower() in _LOWER_CONNECTORS:
            continue
        if not word[0].isupper():
            return False
    return True


def _heading_title(
    text: str, *, allow_unnumbered: bool = True
) -> str | None:
    stripped = text.strip()
    if len(stripped) < 2 or len(stripped) > 120:
        return None
    if _is_footnote_line(stripped):
        return None
    if stripped.lower().startswith("arxiv:"):
        return None
    if stripped[0].islower():
        return None
    if _is_known_heading(stripped):
        return stripped

    numbered = _NUMBERED_HEADING_RE.match(stripped)
    if numbered and _looks_like_heading_rest(numbered.group("rest").strip()):
        return stripped

    if allow_unnumbered and _looks_like_appendix_heading(stripped):
        return stripped
    return None


def _is_reference_heading(text: str) -> bool:
    base = _NUMBERING_PREFIX_RE.sub("", text.strip()).rstrip(".:").strip().lower()
    return base in _REFERENCE_HEADINGS


def _is_hard_heading(text: str) -> bool:
    """A heading strong enough to end the search for an abstract.

    Author affiliations can look like unnumbered headings, so only known
    headings and numbered section headings count here.
    """

    if _is_known_heading(text):
        return True
    numbered = _NUMBERED_HEADING_RE.match(text.strip())
    return bool(
        numbered and _looks_like_heading_rest(numbered.group("rest").strip())
    )


def _is_caption(text: str) -> bool:
    return bool(_CAPTION_RE.match(text.strip()))


def _is_page_number(text: str) -> bool:
    stripped = text.strip()
    return stripped.isdigit() and len(stripped) <= 4


def _looks_like_probable_section_heading(text: str) -> bool:
    stripped = text.strip()
    words = stripped.split()
    if not 2 <= len(words) <= 6:
        return False
    if any(char in stripped for char in ".,;:?!@=%$()[]|"):
        return False
    if any(char.isdigit() for char in stripped):
        return False
    if stripped.isupper():
        return False
    matches = _WORD_RE.findall(stripped)
    if not matches:
        return False
    for word in matches:
        if word.lower() in _LOWER_CONNECTORS:
            continue
        if not word[0].isupper():
            return False
    return True


def _caption_within(lines: list[_Line], index: int, distance: int = 3) -> bool:
    seen = 0
    for cursor in range(index + 1, len(lines)):
        if not lines[cursor].text:
            continue
        seen += 1
        if _is_caption(lines[cursor].text):
            return True
        if seen >= distance:
            return False
    return False


def _caption_kind(text: str) -> str:
    return "table" if text.strip().lower().startswith("table") else "figure_caption"


def _is_title_candidate(text: str) -> bool:
    stripped = text.strip()
    if not 3 <= len(stripped) <= 200:
        return False
    if _is_boilerplate(stripped) or _is_footnote_line(stripped):
        return False
    if _is_known_heading(stripped):
        return False
    if any(symbol in stripped for symbol in "∗†‡⋆"):
        return False
    lowered = stripped.lower()
    if any(marker in lowered for marker in ("@", "http", "www.", "doi:", "arxiv:")):
        return False
    if stripped.endswith(tuple(_TITLE_BAD_PUNCT)):
        return False
    if not stripped[0].isupper() and not stripped[0].isdigit():
        return False
    if stripped.islower():
        return False
    return sum(char.isalpha() for char in stripped) >= 2


def _is_title_continuation(previous: str, following: str) -> bool:
    stripped = following.strip()
    if not stripped or len(stripped) > 150:
        return False
    if "," in stripped or ";" in stripped:
        return False
    if any(symbol in stripped for symbol in "∗†‡⋆"):
        return False
    if _is_boilerplate(stripped) or _is_footnote_line(stripped):
        return False
    if _is_known_heading(stripped):
        return False
    lowered = stripped.lower()
    if any(marker in lowered for marker in ("@", "http", "www.", "doi:", "arxiv:")):
        return False
    if not stripped[0].isupper() and not stripped[0].isdigit():
        return False
    if stripped.endswith("."):
        return False
    previous = previous.rstrip()
    if previous.endswith(("-", ":")):
        return True
    words = previous.split()
    return bool(words and words[-1].lower() in _LOWER_CONNECTORS)


def _find_title(lines: list[_Line]) -> tuple[str | None, int | None, int | None]:
    limit = min(len(lines), 80)
    for index in range(limit):
        text = lines[index].text
        if not text or not _is_title_candidate(text):
            continue
        parts = [text.strip()]
        end = index + 1
        while end < len(lines):
            following = lines[end].text
            if not following:
                break
            if not _is_title_continuation(parts[-1], following):
                break
            parts.append(following.strip())
            end += 1
        return " ".join(parts), index, end
    return None, None, None


def _is_abstract_heading(text: str) -> str | None:
    stripped = text.strip()
    if stripped.lower() == "abstract":
        return ""
    match = re.match(r"^abstract\s*[:.—-]\s*(.*)$", stripped, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _is_abstract_stop(text: str) -> bool:
    lowered = text.strip().lower()
    if _is_footnote_line(text):
        return True
    return lowered.startswith(_ABSTRACT_STOP_PREFIXES)


def _find_abstract(
    lines: list[_Line], start: int
) -> tuple[str | None, int | None, int | None]:
    limit = min(len(lines), start + 250)
    for index in range(start, limit):
        text = lines[index].text
        if not text:
            continue
        initial = _is_abstract_heading(text)
        if initial is None:
            if _is_hard_heading(text):
                return None, None, None
            continue
        body: list[str] = []
        if initial:
            body.append(initial)
        end = index + 1
        while end < len(lines):
            current = lines[end].text
            if not current:
                if body:
                    break
                end += 1
                continue
            if _is_abstract_stop(current):
                break
            if _is_hard_heading(current):
                break
            body.append(current)
            end += 1
        abstract = "\n".join(body).strip()
        if not abstract:
            return None, None, None
        return abstract, index, end
    return None, None, None


def _emit_references(ref_lines: list[_Line], builder: ChannelBuilder) -> None:
    if not ref_lines:
        return
    entries: list[list[_Line]] = []
    current: list[_Line] = []
    for line in ref_lines:
        if _REFERENCE_ENTRY_RE.match(line.text) and current:
            entries.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        entries.append(current)

    for entry in entries:
        text = "\n".join(line.text for line in entry).strip()
        if text:
            builder.add("reference", text, page=entry[0].page)


def _emit_sections(
    lines: list[_Line], start: int, builder: ChannelBuilder
) -> None:
    current_heading: str | None = None
    current_page: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_heading, current_page, current_lines
        if current_heading is None:
            body = "\n".join(current_lines).strip()
            if body:
                builder.add("section", body, page=current_page)
        else:
            body = "\n".join(current_lines).strip()
            text = (
                current_heading
                if not body
                else f"{current_heading}\n\n{body}"
            )
            builder.add("section", text, page=current_page)
        current_heading = None
        current_page = None
        current_lines = []

    index = start
    caption_guard = 0
    while index < len(lines):
        line = lines[index]
        text = line.text
        if not text:
            index += 1
            continue

        heading = _heading_title(text, allow_unnumbered=caption_guard == 0)
        if (
            heading is not None
            and not _is_hard_heading(heading)
            and _caption_within(lines, index)
        ):
            heading = None
        if heading is not None:
            caption_guard = 0
            if current_heading is not None:
                flush()
            if _is_reference_heading(heading):
                ref_lines: list[_Line] = []
                cursor = index + 1
                previous_text: str | None = None
                while cursor < len(lines):
                    candidate = lines[cursor]
                    current = candidate.text
                    if current:
                        if _heading_title(current) is not None:
                            break
                        if (
                            previous_text is not None
                            and _is_page_number(previous_text)
                            and _looks_like_probable_section_heading(current)
                        ):
                            break
                        if not _is_page_number(current):
                            ref_lines.append(candidate)
                        previous_text = current
                    cursor += 1
                _emit_references(ref_lines, builder)
                current_heading = None
                current_page = None
                current_lines = []
                index = cursor
                continue
            current_heading = heading
            current_page = line.page
            current_lines = []
        elif _is_caption(text):
            builder.add(_caption_kind(text), text.strip(), page=line.page)
            caption_guard = 3
        else:
            current_lines.append(text)
            if current_page is None:
                current_page = line.page
            if caption_guard > 0:
                caption_guard -= 1
        index += 1

    flush()


def _flatten(pages: list[_Page]) -> list[_Line]:
    lines: list[_Line] = []
    for page in pages:
        for raw in page.text.splitlines():
            text = raw.replace("\x00", "").strip()
            if not text and lines and not lines[-1].text:
                continue
            lines.append(_Line(page.number, text))
    return lines


@register("pdf-fallback")
def parse_pdf_fallback(data: bytes, source_filename: str) -> ParsedDocument:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"{source_filename}: could not open PDF: {exc}") from exc

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ParseError(f"{source_filename}: encrypted PDF") from exc

    pages: list[_Page] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - skip one malformed page, keep the rest
            text = ""
        pages.append(_Page(number, text))

    if not any(page.text.strip() for page in pages):
        raise ParseError(
            f"{source_filename}: no extractable text (scanned or image-only PDF?)"
        )

    lines = _flatten(pages)
    builder = ChannelBuilder()

    title, title_start, title_end = _find_title(lines)
    if title is not None and title_start is not None:
        builder.add("title", title, page=lines[title_start].page)

    body_start = title_end if title_end is not None else 0
    abstract, abstract_start, abstract_end = _find_abstract(lines, body_start)
    if abstract is not None and abstract_start is not None:
        builder.add("abstract", abstract, page=lines[abstract_start].page)
        body_start = abstract_end if abstract_end is not None else body_start

    _emit_sections(lines, body_start, builder)

    return make_document(
        "pdf-fallback",
        source_filename,
        data,
        builder.finish(),
        page_count=len(pages),
    )
