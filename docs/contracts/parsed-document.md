# ParsedDocument contract (v1)

Status: frozen for Lane 3 and later lanes. This document is normative for the
shape of parse output, channel IDs, and the prompt-facing helper. The code lives
in `src/wikimind/parse/`.

The parse layer turns one source file into one `ParsedDocument` containing
typed, content-addressed `Channel` objects. Channel text is the raw layer (D4).
It must never be pasted into an LLM prompt directly; use
`channels_for_prompt()` (see §7).

## 1. Parser names

Parser names are stable and are part of the memoization key. They must not
change after first use.

| Name | Input | Network | Status |
| --- | --- | --- | --- |
| `text` | `.txt`, `.md` (UTF-8) | none | required |
| `pdf-fallback` | `.pdf` | none | required |
| `grobid` | `.pdf` | GROBID REST | optional |

`parse_file(path)` picks `pdf-fallback` for `.pdf` and `text` for `.txt`/`.md`.
GROBID is never selected automatically. Pass `parser="grobid"` explicitly.

## 2. `ParsedDocument` fields

Pydantic model, `extra="forbid"`, frozen. JSON field order follows this table.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `schema_version` | `int` | no (default `1`) | Contract version. This document describes version `1`. |
| `parser` | `str` | yes | Registered parser name, one of the table above. |
| `source_filename` | `str` | yes | Basename of the source file, with `/` or `\` path parts removed. Not part of any ID. |
| `source_sha256` | `str` | yes | Lowercase SHA-256 hex digest of the raw file bytes. This is the document ID. |
| `source_bytes` | `int` | yes | Length of the raw file in bytes. |
| `page_count` | `int \| null` | no (default `null`) | Number of PDF pages for `pdf-fallback`; `null` for `text` and `grobid`. |
| `channels` | `Channel[]` | yes | Channels in parser emission order. May be empty. |

`source_sha256` is computed from bytes, never from a path or filename. Two
byte-identical files in different directories therefore have the same document
identity.

## 3. `Channel` fields

Pydantic model, `extra="forbid"`, frozen. JSON field order follows this table.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `kind` | `ChannelKind` | yes | One of the six kinds in §4. |
| `ordinal` | `int >= 0` | yes | Position within this kind, starting at `0`, contiguous, in emission order. |
| `text` | `str` | yes | Raw parser text for this channel. Whitespace is parser-specific. |
| `normalized` | `str` | computed | `normalize_text(text)`; always present in serialized output. |
| `channel_id` | `str` | computed | SHA-256 hex digest derived from `kind` + `normalized`; always present. |
| `page` | `int \| null` | no (default `null`) | 1-based page where the channel starts for `pdf-fallback`; `null` for `text` and `grobid`. |

`normalized` and `channel_id` may be supplied when loading JSON. If supplied,
they must equal the values derived from `kind` and `text`; otherwise validation
raises `ValueError`. They are always written on serialization.

`channel.source_ref` is a convenience property returning `channel:<channel_id>`.

### 3.1 Ordinal and ordering rules

- `ordinal` counts within `kind`, not across the document. The first `section`
  is `0`, the second is `1`; the first `reference` is `0`.
- Ordinals must be contiguous from `0` in `channels` order. A document with
  `section` ordinals `0, 1, 2` is valid; `0, 2` is not.
- `channels` order is the parser's deterministic emission order. For
  `pdf-fallback` this is document order. For `grobid` a section is emitted
  before figures and nested divisions inside it.
- Identical `(kind, normalized)` pairs produce the same `channel_id`. Within one
  document, use `(channel_id, ordinal)` or the list position when uniqueness is
  required. Across documents, identical text intentionally shares an ID.

## 4. Kind enum

Exactly these string values are valid:

| Value | Meaning |
| --- | --- |
| `title` | Document title. At most one per document in the current parsers. |
| `abstract` | Abstract body, without the literal `Abstract` heading. |
| `section` | A body section. When a heading is detected, `text` is `heading + "\n\n" + body`; otherwise it is the body text alone. |
| `table` | A table caption or table text. |
| `figure_caption` | A figure caption. |
| `reference` | One bibliography entry, or the whole bibliography block if entries cannot be split. |

## 5. Normalization and ID derivation (exact)

These formulas are the ID contract. They must not change for
`schema_version = 1`.

```python
import hashlib
import unicodedata

def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())

def channel_id(kind: str, normalized: str) -> str:
    payload = kind + "\x1f" + normalized
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def document_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
```

Notes:

- `str.split()` splits on every Unicode whitespace character, including
  non-breaking space. The join uses a single ASCII space.
- Normalization happens after NFC, so canonically equivalent input yields the
  same `normalized` and the same ID.
- `\x1f` is the ASCII unit separator, one character.
- Encoding is UTF-8. Output hex is lowercase, 64 characters.
- `kind` is the enum value, e.g. `"figure_caption"`, not the enum object.

Example: for `kind="section"` and text `"  Hello\u00a0World  "`, the normalized
text is `"Hello World"` and the ID is
`sha256(b"section\x1fHello World").hexdigest()`.

## 6. JSON round-trip

`ParsedDocument.model_dump_json()` produces the canonical JSON form. Loading it
with `ParsedDocument.model_validate_json()` and dumping again is byte-identical
for the same input.

There are no timestamps, absolute paths, random values, or environment-dependent
fields. A document can be stored and revalidated without the source file.

Example:

```json
{
  "schema_version": 1,
  "parser": "text",
  "source_filename": "contradiction-note.txt",
  "source_sha256": "4ef572f4fd21de0d5e1865f0055a4a11b60fdf82ec0ec14ebef3b8ae3bf22756",
  "source_bytes": 725,
  "page_count": null,
  "channels": [
    {
      "kind": "section",
      "ordinal": 0,
      "text": "SYNTHETIC TEST FIXTURE - NOT A REAL CITATION\n...",
      "normalized": "SYNTHETIC TEST FIXTURE - NOT A REAL CITATION ...",
      "channel_id": "<sha256 hex>",
      "page": null
    }
  ]
}
```

## 7. Prompt helper (D4)

`channels_for_prompt(channels, char_budget, *, per_channel_budget=None) -> str`
is the only sanctioned way channel text enters an LLM prompt.

### 7.1 Tag shape

Each channel becomes exactly:

```text
<untrusted source="channel:<channel_id>" kind="<kind>">text</untrusted>
```

Blocks are separated by a single newline. `source` is always
`channel:` followed by the 64-character hex ID. `kind` is the enum value.

### 7.2 Escaping

Channel text is escaped before insertion so it cannot close or open the
wrapper:

| Character | Replacement |
| --- | --- |
| `&` | `&amp;` |
| `<` | `&lt;` |
| `>` | `&gt;` |

Escaping happens in that order. Quotes are not escaped because the text is
element content, not an attribute value. The escaped form is what the budget
counts.

### 7.3 Budget semantics

- `char_budget` is the **total** maximum length of the returned string,
  including tags, newline separators, and truncation markers. It is a character
  count, not a byte count.
- `per_channel_budget` is an optional cap on the escaped text inside one
  channel. If omitted, each channel gets an equal share of the space remaining
  after all wrapper tags and separators:
  `max(0, (char_budget - sum(wrapper_lengths) - (n - 1)) // n)`.
- If that equal share is zero because the wrappers alone consume the budget, the
  helper falls back to giving the first channel the remaining total budget;
  later channels are omitted. A tight budget can therefore still produce one
  valid truncated block.
- If a channel's escaped text is longer than its cap, it is cut and the literal
  marker `[truncated]` is appended, keeping the total text length at the cap.
  The marker counts toward the cap.
- If a channel's wrapper plus a valid truncated payload cannot fit in the
  remaining total budget, that channel and all later channels are omitted. A
  partial tag is never emitted.
- Empty channel text is allowed and still produces a wrapper.
- `char_budget=0` or an empty channel list returns `""`.
- Negative budgets raise `ValueError`.
- Channels are never reordered. The caller controls order by the input order.

Example with a large budget:

```python
from wikimind.parse import Channel, channels_for_prompt

channel = Channel(kind="section", ordinal=0, text="Hello <world> & goodbye")
channels_for_prompt([channel], 10_000)
# '<untrusted source="channel:<id>" kind="section">Hello &lt;world&gt; &amp; goodbye</untrusted>'
```

## 8. Skip-unchanged filter

```python
filter_known(documents, known_sha256s: set[str]) -> list[ParsedDocument]
```

Returns the documents whose `source_sha256` is not in the injected set, in the
same order. It is a pure function: no database, no I/O, no mutation of the
input. A caller supplies the persisted SHA set later.

## 9. Parser behavior

### 9.1 `text`

- Decodes UTF-8; a UTF-8 BOM is stripped. Invalid UTF-8 raises `ParseError`.
- Emits one `section` channel containing the decoded file text.
- A whitespace-only file emits zero channels.
- No title extraction; Markdown is treated as plain text.

### 9.2 `pdf-fallback`

- Uses pypdf to extract text page by page, fully offline.
- Emits `title` when a plausible title line is found.
- Emits `abstract` when an `Abstract` heading is found before the first hard
  section heading.
- Detects numbered headings (`1 Introduction`, `2.1 ...`), known headings
  (`Abstract`, `References`, `Acknowledgments`, ...), and appendix-letter
  headings (`A Implementation Details`). Other unnumbered headings are not
  detected; their text stays in the preceding section.
- Text before the first detected section heading is not emitted (it is usually
  author metadata or footnotes). If no heading is found, the remaining body
  becomes one `section`.
- Single-line captions starting `Figure N:`/`Table N:` (or `.`/`-`/`|` after
  the number) become `figure_caption`/`table` channels and are removed from the
  section body. Caption continuation lines are not reconstructed.
- A `References`/`Bibliography` block is split into `reference` channels on
  `[n]` or short `n.` entry starts (1–3 digits, so years and volume numbers do
  not create false entries). If no entry start is found, the whole block is one
  `reference` channel.
- Raises `ParseError` for unreadable, encrypted, or image-only PDFs with no
  extractable text.
- `page` is 1-based for every emitted channel; `page_count` is the number of
  PDF pages.

The heuristics are intentionally frozen once used: the parser name is a memo
key, so changing sectioning behavior changes IDs for the same bytes. Treat a
behavior change as a new parser generation, not an edit under the same name.

### 9.3 `grobid`

- Requires `WIKIMIND_GROBID_URL` (for example `http://localhost:8070`).
- POSTs the PDF as multipart field `input` to
  `<base>/api/processFulltextDocument`, accepting TEI XML.
- Parses TEI with `xml.etree.ElementTree` and the TEI namespace
  `http://www.tei-c.org/ns/1.0`.
- Emits `title` from `titleStmt/title`, `abstract` from `abstract`,
  `section` from body divisions (`head` + direct paragraphs), `figure_caption`
  or `table` from `figure` elements, and `reference` from each `biblStruct`.
- `page` is `null`; `page_count` is `null`.
- Missing configuration raises `GrobidUnavailableError` (a `ParseError`).
  Network and HTTP failures raise `GrobidError`.
- Start the service with:
  `docker run -d -p 8070:8070 lfoppiano/grobid:0.8.1`

## 10. Determinism

The release requirement is: same input bytes + same parser name + same
implementation -> byte-identical channel IDs on every run. The offline suite
parses every fixture twice and compares IDs.

Within that guarantee:

- No timestamps, randomness, environment values, or filesystem paths enter the
  output.
- Channel order and ordinals are deterministic.
- `pdf-fallback` output can change if the installed pypdf version changes its
  extraction. The parser name is therefore an immutable algorithm version:
  do not edit the heuristics under the same name after output has been
  persisted. Memoization should include the parser name and source SHA.
- `grobid` output depends on the GROBID service version; it is optional and not
  part of the offline determinism gate.

## 11. CLI

```text
python -m wikimind.parse.cli run <file> --json
python -m wikimind.parse.cli run <file> --parser grobid --json
python -m wikimind.parse.cli grobid-check
```

`run` prints the canonical JSON with `--json`, otherwise a short summary.
`grobid-check` probes `<WIKIMIND_GROBID_URL>/api/health` and exits non-zero if
the service is missing or unreachable. After the trunk CLI merges, the same
commands appear as `wikimind parse run` and `wikimind parse grobid-check`.
