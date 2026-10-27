# LLM client contract (LANE 2)

Status: implemented in `src/wikimind/llm/` and `src/wikimind/prices.py`.
Audience: LANES 3-5 and any later caller of the model access layer.

This document is the interface contract for every model call in WikiMind.
DECISIONS.md wins on conflict. Relevant frozen decisions:

- **D12** — Flash-class model for distilling/updating; Pro-class only for
  the final answer and entailment checks. V0-NOTES approved deviation #1
  waives the Pro-class answerer for V0: both arms use one Gemini-class
  drafter model, which is why the lane pins one Gemini model. The
  drafter is `gemini-3.1-flash-lite` (V0-NOTES Ruling 12, 2026-10-27);
  the original `gemini-2.5-flash` pin is permanently retired upstream.
- **V0-NOTES Ruling 5 / Rulings 8 / 10 / 12 / 13** — one Gemini-class
  model for BOTH the distill and QA arms (identical model), local
  `BAAI/bge-small-en-v1.5` embeddings, and a non-Gemini judge. The
  drafter is re-pinned by Ruling 12 to `gemini-3.1-flash-lite`: the
  live pre-flight smoke of 2026-10-27 proved `gemini-2.5-flash`
  permanently retired (404 for new users) and the vendor-named
  successor `gemini-3.8-flash` unusable (503 x3, 504 at 180 s).
  Ruling 12's principle: a model pin requires a working live inference
  path through our own client, proven by live smoke — catalog listing
  is not liveness. The judge is re-pinned by Ruling 13 to
  `nvidia/nemotron-3-ultra-550b-a55b:free` (the Ruling 10 pin's free
  route is per-account dead upstream). Binding constraints for any
  future judge re-pin: never a Gemini model, `:free` tier, competent at
  entailment checking.
- **Ruling 6 / Ruling 11** — every call is metered; shadow cost uses
  production list prices even when the call itself is free. Ruling 11
  (2026-10-27) fixed the pricing convention: shadow cost prices the
  call that was actually made. If the called model has a production
  list price, use it — both V0 arms run `gemini-3.1-flash-lite`, so
  both arms price at its list; no arm is priced at a model it does not
  run. If the endpoint is `:free` (no list price), price the pinned
  paid twin recorded in `prices.py`. Arm tags travel in the cost event
  meta. This resolves the earlier Ruling 6 answer-role question: the
  Pro-class shadow price is never applied in V0.
- **D8** — distilled pages and raw source text are untrusted. Callers must
  label untrusted text in prompts; the model layer does not do this for
  you.

## 1. Protocols

```python
from wikimind.llm import LLMClient, Embedder, LLMResult, Usage


class LLMClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        response_schema: dict | type[pydantic.BaseModel] | None,
        call_site: str,
        max_retries: int = 3,
        meta: dict | None = None,  # additive extension; see below
    ) -> LLMResult: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

`LLMResult` is a frozen dataclass:

```python
@dataclass(frozen=True)
class LLMResult:
    json: dict
    usage: Usage


@dataclass(frozen=True)
class Usage:
    in_tokens: int
    out_tokens: int
    # total_tokens property = in_tokens + out_tokens
```

`meta` is an additive optional keyword beyond the five keywords in the
lane card. It is how A/B arm tags and model versions reach the cost
ledger. Callers using the `LLMClient` protocol may pass `meta=`; all
implementations in this lane accept it. `meta` must be a JSON-safe dict
with string keys and finite values; the meter validates it before the
network call so a billed response cannot fail during ledger
serialization.

`max_retries` must be in `1..3`; provider clients reject larger values
before any network call. This is a hard safety cap, not a suggestion.

`response_schema` is either a JSON-schema `dict` or a Pydantic model
class. `None` is allowed but discouraged: both provider clients still
request JSON mode and will fail with `ResponseFormatError` if the model
returns non-JSON. Pydantic schemas are validated locally with
`model_validate` and dumped with `mode="json"` so `datetime`, `Decimal`,
and UUID fields remain JSON-serializable; dict schemas are passed to the
provider as a hint and are **not** locally validated.

### JSON mode

- Gemini: `response_mime_type="application/json"` plus
  `response_json_schema=<dict>` or `response_schema=<Pydantic class>`.
- Judge: OpenRouter `response_format={"type": "json_object"}`. If a
  schema is supplied, its JSON form is appended to the system prompt
  because free-model structured-output support varies. The judge always
  parses the returned content as a JSON object.
- Fenced JSON (```` ```json ... ``` ````) is tolerated. If strict parsing
  fails, the parser scans for the first valid JSON object, so prose
  containing braces does not defeat extraction; any non-object JSON is
  rejected.
- A 2xx response that cannot be parsed or fails local Pydantic validation
  raises `ResponseFormatError` **after** emitting one billable
  `CostEvent` (see §2).

## 2. CostEvent — the audit spine

Every billable provider response (HTTP 2xx) emits exactly one
`CostEvent`, including 2xx responses that later fail JSON parsing or
local schema validation:

```python
class CostEvent(BaseModel):
    call_site: str  # must start with a canonical prefix
    model: str  # the model ID actually requested
    in_tokens: int
    out_tokens: int
    unit_price: ModelPrice  # USD per 1M tokens, input and output
    shadow_cost_usd: float
    meta: dict
    timestamp: datetime  # UTC, ISO 8601 in JSONL
```

`CostEvent` validates price integrity on construction:

- `unit_price` must equal `price_for(model)` from `prices.py`.
- `shadow_cost_usd` must equal the token-based calculation below.
- `meta` must be a JSON-safe dict with string keys and finite values.
- `call_site` must use a canonical prefix.

Callers cannot override `unit_price`; `CostMeter.record` derives it from
`prices.py`. Unknown models raise `PricingError` in the client/embedder
constructor, before any network request. When reading an existing
ledger, `read_cost_events` validates `shadow_cost_usd` against the
**stored** `unit_price` and does not require the stored price to equal
today's `prices.py` entry, so historical ledgers survive price updates.

`ModelPrice` is:

```json
{"input_per_mtok": 0.25, "output_per_mtok": 1.50, "currency": "USD"}
```

`shadow_cost_usd` is computed as:

```text
(in_tokens * input_per_mtok + out_tokens * output_per_mtok) / 1_000_000
```

rounded to 12 decimal places. It is always the **production list price**,
even when the provider call is free. Per V0-NOTES **Ruling 11
(2026-10-27)**, the price used is the one for the model actually called:
both V0 arms run `gemini-3.1-flash-lite` and price at its list; a `:free`
endpoint (the judge) prices its pinned paid twin from `prices.py`, and
the judge's actual endpoint cost is recorded as
`meta["actual_cost_usd"] = 0.0`. No arm is ever priced at a model it
does not run.

Example JSONL line:

```json
{"call_site":"judge.grade","model":"nvidia/nemotron-3-ultra-550b-a55b:free","in_tokens":1200,"out_tokens":80,"unit_price":{"input_per_mtok":0.6,"output_per_mtok":2.4,"currency":"USD"},"shadow_cost_usd":0.000912,"meta":{"arm":"B","provider":"openrouter","pricing_basis":"paid_twin","paid_twin":"nvidia/nemotron-3-ultra-550b-a55b","actual_cost_usd":0.0},"timestamp":"2026-10-27T12:00:00Z"}
```

### Call-site taxonomy

`call_site` MUST start with one of these prefixes (validated by
`validate_call_site` and `CostEvent`):

| Prefix | Meaning | Examples |
| --- | --- | --- |
| `l1.*` | Level 1 distillation, one reference node per document | `l1.distill`, `l1.claim_extract` |
| `l2.*` | Level 2 entity/concept/synthesis compilation | `l2.merge`, `l2.update` |
| `qa.*` | Query-time rewriting/answer synthesis | `qa.rewrite`, `qa.answer`, `qa.smoke` |
| `judge.*` | OpenRouter judge calls | `judge.grade`, `judge.entail`, `judge.smoke` |
| `embed.*` | Local embedding calls | `embed.ingest`, `embed.query`, `embed.smoke` |

The suffix is yours to choose, but keep it stable: the `wikimind llm
costs` report aggregates by the full `call_site`.

A/B arm tags and model versions travel in `meta`, for example:

```python
client.complete(..., meta={"arm": "B", "model_version": "gemini-3.1-flash-lite"})
```

Gemini adds `provider`, `model_version`, `response_id`,
`thinking_tokens`, `usage_reported`, `usage_invalid`, `parse_ok`,
`schema_checked`, and `schema_ok` automatically. The judge adds
`provider`, `endpoint`, `pricing_basis`, `paid_twin`,
`actual_cost_usd`, `response_model`, `generation_id`, `finish_reason`,
`reasoning_tokens`, `usage_reported`, `usage_invalid`, `parse_ok`,
`schema_checked`, and `schema_ok`. Missing/malformed usage is coerced to
zero tokens and flagged with `usage_invalid=true`, never dropped.

### Sinks

- `CostSink` protocol: `emit(event: CostEvent) -> None` and
  `preflight() -> None`.
- Default: `JsonlCostSink`, append-only JSONL at `costs.jsonl` in the
  current working directory, or `WIKIMIND_COSTS_PATH` if set. Every line
  is passed through the redactor before it is written, so key material
  can never reach the ledger even if a caller leaked a secret into
  `meta` (defense in depth; never rely on it — keep keys out of `meta`).
- `InMemorySink`: tests and session reports.
- `DbCostSink`: **interface stub only**. `emit` and `preflight` raise
  `NotImplementedError`; trunk wires the Postgres sink in a later lane.
- `CostMeter.events` keeps the current process's events so the smoke
  command can print the exact session report.
- `CostMeter.preflight()` opens the sink before the first billable call.
  Clients validate `meta` separately before preflight/network; a client
  never calls the provider until both succeed.
- The event is appended to `CostMeter.events` **before** `sink.emit()`;
  if the sink then fails, the in-memory audit event survives and the
  `CostSinkError` still propagates. A successful emit is one write of
  `line + "\n"`; readers ignore a trailing partial line from a crash.

Failed attempts (429/5xx) do not emit a `CostEvent`: providers do not
report billable usage for those failures. The final error is raised after
retries. A 2xx response emits exactly one event even if parsing or
schema validation fails; its `meta` records `parse_ok`/`schema_ok` false
and zero tokens if usage is absent. The meter refuses unknown model IDs
(`PricingError`) so an unmetered call cannot slip through.

## 3. Retry semantics

`max_retries` is the **total attempt count**, not retries after the
initial call. Default `3` means at most three attempts. Values outside
`1..3` raise `ValueError` before any network call.

- Retry **only** HTTP 429 and 5xx.
- Gemini maps `google.genai.errors.APIError.code` (and
  `RESOURCE_EXHAUSTED` / `UNAVAILABLE` status strings) to a status code.
- Judge retries on the HTTP response status.
- Do **not** retry other 4xx, 3xx, invalid JSON, missing keys, or
  transport errors (the judge wraps transport errors in `JudgeError`
  immediately and rejects any non-2xx response).
- Backoff is deterministic exponential: 0.5s, 1.0s (three attempts
  maximum). Sleep is injectable for keyless tests.
- Exhaustion raises `RetryExhaustedError`. A non-retryable provider error
  raises `GeminiError` / `JudgeError`. Invalid JSON, a malformed 2xx
  payload, or local schema validation raises `ResponseFormatError`,
  after metering the 2xx call. `JudgeError` is reserved for HTTP/transport
  failures.

## 4. Pricing and pinned models

All prices are USD per 1,000,000 tokens. `prices.py` is the single source
of truth. `PRICES_VERIFIED_ON = "2026-10-23"`.

| Model | Role | Input | Output | Source |
| --- | --- | --- | --- | --- |
| `gemini-3.1-flash-lite` | distill (`l1.*`) and QA (`qa.*`) | $0.25 | $1.50 | Google Gemini API pricing (`ai.google.dev/gemini-api/docs/pricing`), retrieved 2026-10-27 (standard paid tier, text) |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | judge (`judge.*`) | $0 actual | $0 actual | OpenRouter model catalog, probed live 2026-10-27 (working free route) |
| `nvidia/nemotron-3-ultra-550b-a55b` | judge shadow (paid twin) | $0.60 | $2.40 | OpenRouter model page, probed 2026-10-27 |
| `BAAI/bge-small-en-v1.5` | local embeddings | $0 | $0 | local CPU model |
| `fake-llm` | keyless tests | $0 | $0 | test fake |
| `fake-embedder` | keyless tests | $0 | $0 | test fake |

### Why this judge

Pin history: the card's default, `deepseek/deepseek-chat-v3-0324:free`,
was dropped from OpenRouter (Ruling 10 re-pinned to
`nvidia/nemotron-3-super-120b-a12b:free`); the live pre-flight smoke of
2026-10-27 then showed that pin's free route is per-account dead (404
from the Nvidia provider while the catalog still lists the slug —
catalog listing is not liveness). Ruling 13 (2026-10-27) re-pins the
judge to `nvidia/nemotron-3-ultra-550b-a55b:free`, proven working via
Nvidia (~2 s) through our own client during the same smoke. The
`nvidia/nemotron-3.5-lightning:free` candidate was disqualified: it
returned `content=None`. It is **never** a Gemini model, so the binding
family-difference constraint holds (a guard test enforces this). Its
paid twin is used for shadow cost, per Ruling 11: free routes are
priced at the paid twin, never at zero.

Free-tier reality: OpenRouter `:free` models have low daily caps
(roughly 50 requests/day without the one-time $10 credit; roughly
1000/day with it, per V0-NOTES Ruling 8). The judge workload may need to
span multiple days; never reduce scope. Gemini 2.5 Flash free-tier calls
are rate-limited too; the meter still prices them at list price.

### Model pin

`GEMINI_MODEL = "gemini-3.1-flash-lite"` is used for both arms (pinned
by V0-NOTES Ruling 12, 2026-10-27). The model stack is frozen before
tuning (V0-NOTES). A pin is only valid once a live smoke through our own
client proves the exact ID answers; catalog listing is not liveness.
This lane reports live-smoke failures rather than silently changing
models; a re-pin is a governance ruling plus a `prices.py` update with
source and retrieval date, never a silent substitution. Every client and embedder calls `price_for`
in its constructor, so an unpriced model fails before any network
request.

## 5. Keys and redaction

- Keys are read from the environment only:
  `GEMINI_API_KEY` and `OPENROUTER_API_KEY`.
- The CLI calls `load_dotenv()` first. It reads UTF-8/UTF-8-BOM `.env`
  (or `WIKIMIND_ENV_FILE`) without overriding non-empty environment
  values; empty values count as unset, and unquoted ` # comment` tails
  are stripped. `.env` is gitignored; values are never logged.
- `redact_secrets(text, extra=...)` replaces exact environment key
  values, explicit keys passed to a client, and common key shapes
  (`AIza...`, `sk-or-v1-...`, `Bearer ...` including `+/=` tails,
  `api_key=...`).
- Every error raised by this layer passes its message through the
  redactor, including provider errors that echo an explicitly supplied
  key. The CLI redacts unexpected exceptions at the command boundary,
  and `JsonlCostSink` redacts each ledger line before writing it. The
  Bearer-token and `api_key=` shape patterns stop at JSON
  string/structural characters so redacting a serialized JSON line
  cannot corrupt it. Never put a key in `meta`, a prompt, a URL, or a
  log line.
- Tests assert that keys never appear in log output or in raised
  exception messages.

## 6. Embedder

```python
from wikimind.llm import SentenceTransformerEmbedder

embedder = SentenceTransformerEmbedder(
    meter=meter,
    model_name="BAAI/bge-small-en-v1.5",
    device="cpu",
    call_site="embed.ingest",
)
vectors = embedder.embed(["text one", "text two"])  # list[list[float]], 384 each
```

- `normalize_embeddings=True`, `convert_to_numpy=True`,
  `show_progress_bar=False`.
- Model is loaded lazily on first `embed` and cached by Hugging Face.
  Set `WIKIMIND_EMBED_CACHE` or pass `cache_folder` to relocate the
  cache.
- Output dimension is enforced as 384 (`EMBED_DIM`).
- Every non-empty embedding batch emits a `CostEvent` with
  `shadow_cost_usd = 0`, `in_tokens ≈ characters / 4`, `out_tokens = 0`,
  and meta `{"texts": N, "dim": 384}`. Empty input returns `[]` without
  loading the model or emitting an event.
- The embedder validates its model price in the constructor and calls
  `meter.preflight()` before loading/encoding.
- `FakeEmbedder` is deterministic: SHAKE-256 of the UTF-8 text expanded
  to 384 floats, L2-normalized. Same text in -> same vector out across
  runs and platforms. No network. Empty input emits no event.

## 7. Fakes

`FakeLLM(responses, usage=None, model="fake-llm", meter=None)`:

- `responses` is a queue of `dict`, JSON `str`, `LLMResult`, `Exception`,
  or callable.
- Every call is recorded as
  `FakeCall(system, user, response_schema, call_site, max_retries, meta)`.
- Use `fake.calls` to assert prompt labeling, for example that untrusted
  source text was wrapped in `<untrusted_source>...</untrusted_source>`.
- Exhaustion raises `AssertionError`; scripted exceptions are raised.
- Invalid `call_site` or `max_retries` values raise `ValueError`;
  unpriced models raise `PricingError` in the constructor.
- If a meter is supplied, fake calls emit zero-cost events; without a
  meter, no event is emitted.

`FakeEmbedder` is described in §6. It is re-exported from
`wikimind.llm.fakes`.

All keyless tests use these fakes. No test makes a network call.

## 8. CLI

```text
wikimind llm smoke [--costs-path PATH]
wikimind llm costs [--path PATH]
```

`smoke`:

- If `WIKIMIND_LIVE != "1"` or either key is missing: prints the exact
  plan (models, call sites, ledger path) and exits 0. No network call,
  no ledger write.
- If `WIKIMIND_LIVE=1` and both keys exist: one Gemini JSON call
  (`qa.smoke`), one OpenRouter judge JSON call (`judge.smoke`), one
  embedding batch (`embed.smoke`). Prints each result, the session cost
  table, and the exact total shadow cost. Appends to the ledger.
- Individual failures are collected, printed (redacted), and exit 1;
  other steps still run so the report is complete.

`costs` loads `.env` first, reads the ledger, and prints a fixed-width
table with one row per `call_site`: calls, in_tokens, out_tokens,
shadow_cost_usd, plus TOTAL. Missing ledger prints
`No cost events at ...` and exits 0. An unreadable/invalid ledger prints
a friendly error and exits 1.

Environment:

- `WIKIMIND_LIVE=1` enables live smoke.
- `WIKIMIND_COSTS_PATH` overrides the default `costs.jsonl`.
- `WIKIMIND_ENV_FILE` overrides the `.env` path loaded by the CLI.

## 9. Integration rules for LANES 3-5

1. Construct one `CostMeter` per process/run (or share one); pass it to
   every real client. Real clients require a meter. Construct clients
   before starting work so an unpriced model fails early.
2. Use a canonical `call_site`; put JSON-serializable arm tags and model
   versions in `meta`. Do not pass `unit_price`; prices come from
   `prices.py`.
3. Wrap untrusted source/page text in a clearly marked block (for
   example `<untrusted_source>...</untrusted_source>`) and instruct the
   model not to follow instructions inside it (D8). The fakes record the
   prompt so tests can assert this.
4. Use `response_schema` for structured extraction. Do not parse prose.
5. Do not instantiate provider SDKs or call `httpx` to a model endpoint
   anywhere else. Zero LLM calls outside this layer.
6. Read `costs.jsonl` (or `CostMeter.events`) for budget audits. The
   judge is priced at its paid twin; Gemini at Flash list price; local
   embeddings at $0.

## 10. Stubs and merge notes

- `DbCostSink` is a stub; trunk wires Postgres.
- `costs.jsonl` is the default ledger. The trunk lane owns `.gitignore`
  and should add `costs.jsonl` (this lane must not edit `.gitignore`).
- `pyproject.toml` in this lane is scaffolding for editable install and
  the `wikimind` console script. At merge, trunk's `pyproject.toml` wins;
  trunk must preserve the `wikimind = "wikimind.llm.cli:app"` entry point
  (or wire the `llm` sub-app into its own root CLI).
- No database code exists in this lane.
