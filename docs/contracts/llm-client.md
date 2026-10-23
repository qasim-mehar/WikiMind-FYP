# LLM client contract (LANE 2)

Status: implemented in `src/wikimind/llm/` and `src/wikimind/prices.py`.
Audience: LANES 3-5 and any later caller of the model access layer.

This document is the interface contract for every model call in WikiMind.
DECISIONS.md wins on conflict. Relevant frozen decisions:

- **D12** — Flash-class model for distilling/updating; Pro-class only for
  the final answer and entailment checks. V0-NOTES approved deviation #1
  waives the Pro-class answerer for V0: both arms use
  `gemini-2.5-flash`, which is why the lane card pins one Gemini model.
- **V0-NOTES Ruling 5 / Ruling 8** — `gemini-2.5-flash` for BOTH the
  distill and QA arms (identical model), local `BAAI/bge-small-en-v1.5`
  embeddings, and a non-Gemini judge. Ruling 8 prefers a DeepSeek-class
  OpenRouter `:free` model; Qwen- or Llama-class are acceptable
  fallbacks.
- **Ruling 6** — every call is metered; shadow cost uses production list
  prices even when the call itself is free. **Open conflict to resolve at
  merge:** Ruling 6 says answer-role calls are shadow-priced at
  `gemini-2.5-pro` list price, while this lane card says to price
  `gemini-2.5-flash` for both arms. This implementation follows the lane
  card (Flash price for `qa.*`). Honoring Ruling 6 would require a Pro
  price plus a role-based shadow-price override; that is a human
  decision, not a silent code change.
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
        meta: dict | None = None,   # additive extension; see below
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
implementations in this lane accept it.

`response_schema` is either a JSON-schema `dict` (both clients support
this) or a Pydantic model class. `None` is allowed but discouraged: both
provider clients still request JSON mode and will fail with
`ResponseFormatError` if the model returns non-JSON.

### JSON mode

- Gemini: `response_mime_type="application/json"` plus
  `response_json_schema=<dict>` or `response_schema=<Pydantic class>`.
- Judge: OpenRouter `response_format={"type": "json_object"}`. If a
  schema is supplied, its JSON form is appended to the system prompt
  because free-model structured-output support varies. The judge always
  parses the returned content as a JSON object.
- Fenced JSON (```` ```json ... ``` ````) is tolerated; any non-object
  JSON is rejected.

## 2. CostEvent — the audit spine

Every successful real call emits exactly one `CostEvent`:

```python
class CostEvent(BaseModel):
    call_site: str                 # must start with a canonical prefix
    model: str                     # the model ID actually requested
    in_tokens: int
    out_tokens: int
    unit_price: ModelPrice         # USD per 1M tokens, input and output
    shadow_cost_usd: float
    meta: dict
    timestamp: datetime            # UTC, ISO 8601 in JSONL
```

`ModelPrice` is:

```json
{"input_per_mtok": 0.30, "output_per_mtok": 2.50, "currency": "USD"}
```

`shadow_cost_usd` is computed as:

```text
(in_tokens * input_per_mtok + out_tokens * output_per_mtok) / 1_000_000
```

rounded to 12 decimal places. It is always the **production list price**,
even when the provider call is free. The judge's actual endpoint cost is
recorded as `meta["actual_cost_usd"] = 0.0`.

Example JSONL line:

```json
{"call_site":"judge.grade","model":"nvidia/nemotron-3-super-120b-a12b:free","in_tokens":1200,"out_tokens":80,"unit_price":{"input_per_mtok":0.3,"output_per_mtok":2.4,"currency":"USD"},"shadow_cost_usd":0.000552,"meta":{"arm":"B","provider":"openrouter","pricing_basis":"paid_twin","paid_twin":"nvidia/nemotron-3-super-120b-a12b","actual_cost_usd":0.0},"timestamp":"2026-10-23T12:00:00Z"}
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
client.complete(..., meta={"arm": "B", "model_version": "gemini-2.5-flash"})
```

Gemini adds `provider`, `model_version`, `response_id`, and
`thinking_tokens` automatically. The judge adds `provider`, `endpoint`,
`pricing_basis`, `paid_twin`, `actual_cost_usd`, `response_model`,
`generation_id`, `finish_reason`, and `reasoning_tokens`.

### Sinks

- `CostSink` protocol: `emit(event: CostEvent) -> None`.
- Default: `JsonlCostSink`, append-only JSONL at `costs.jsonl` in the
  current working directory, or `WIKIMIND_COSTS_PATH` if set.
- `InMemorySink`: tests and session reports.
- `DbCostSink`: **interface stub only**. `emit` raises
  `NotImplementedError`; trunk wires the Postgres sink in a later lane.
- `CostMeter.events` keeps the current process's events so the smoke
  command can print the exact session report.

Failed attempts do not emit a CostEvent: providers do not report billable
usage for a 429/5xx failure. The final error is raised after retries.
Successful calls emit exactly one event. The meter refuses unknown model
IDs (`PricingError`) so an unmetered call cannot slip through.

## 3. Retry semantics

`max_retries` is the **total attempt count**, not retries after the
initial call. Default `3` means at most three attempts.

- Retry **only** HTTP 429 and 5xx.
- Gemini maps `google.genai.errors.APIError.code` (and
  `RESOURCE_EXHAUSTED` / `UNAVAILABLE` status strings) to a status code.
- Judge retries on the HTTP response status.
- Do **not** retry other 4xx, invalid JSON, missing keys, or transport
  errors (the judge wraps transport errors in `JudgeError` immediately).
- Backoff is deterministic exponential: 0.5s, 1.0s, 2.0s, ... capped at
  8s. Sleep is injectable for keyless tests.
- Exhaustion raises `RetryExhaustedError`. A non-retryable provider error
  raises `GeminiError` / `JudgeError`. Invalid JSON raises
  `ResponseFormatError`.

## 4. Pricing and pinned models

All prices are USD per 1,000,000 tokens. `prices.py` is the single source
of truth. `PRICES_VERIFIED_ON = "2026-10-23"`.

| Model | Role | Input | Output | Source |
| --- | --- | --- | --- | --- |
| `gemini-2.5-flash` | distill (`l1.*`) and QA (`qa.*`) | $0.30 | $2.50 | Google Gemini API pricing (`ai.google.dev/gemini-api/docs/pricing`), corroborated by trackers refreshed 2026-10-11 / 2026-10-17 |
| `nvidia/nemotron-3-super-120b-a12b:free` | judge (`judge.*`) | $0 actual | $0 actual | OpenRouter model catalog (`openrouter.ai/api/v1/models`), fetched 2026-10-23 |
| `nvidia/nemotron-3-super-120b-a12b` | judge shadow (paid twin) | $0.30 | $2.40 | same OpenRouter catalog fetch |
| `BAAI/bge-small-en-v1.5` | local embeddings | $0 | $0 | local CPU model |

### Why this judge

The card's default, `deepseek/deepseek-chat-v3-0324:free`, was checked at
pin time and is no longer listed by OpenRouter. The 2026-10-23 catalog
contained **no** DeepSeek, Qwen, or Llama `:free` variants. The closest
free, non-Gemini model with advertised structured-output support is
`nvidia/nemotron-3-super-120b-a12b:free`: a 120B total / 12B active MoE
with 262K context. It is **never** a Gemini model, so the binding
family-difference constraint holds. Its paid twin is used for shadow
cost. **Flag for human:** NVIDIA is outside Ruling 8's enumerated
DeepSeek/Qwen/Llama fallback families; the card's "closest such model"
instruction was followed, but the human should confirm this choice.

Free-tier reality: OpenRouter `:free` models have low daily caps
(roughly 50 requests/day without the one-time $10 credit; roughly
1000/day with it, per V0-NOTES Ruling 8). The judge workload may need to
span multiple days; never reduce scope. Gemini 2.5 Flash free-tier calls
are rate-limited too; the meter still prices them at list price.

### Model pin

`GEMINI_MODEL = "gemini-2.5-flash"` is used for both arms. The model
stack is frozen before tuning (V0-NOTES). If the exact Gemini ID is
unavailable for a key, V0-NOTES pre-authorizes substituting the current
Flash-class equivalent, but that is a human decision; this lane defaults
to the frozen ID and will report a live-smoke failure rather than
silently changing models.

## 5. Keys and redaction

- Keys are read from the environment only:
  `GEMINI_API_KEY` and `OPENROUTER_API_KEY`.
- The CLI calls `load_dotenv()` first. It reads `.env` (or
  `WIKIMIND_ENV_FILE`) without overriding existing environment values.
  `.env` is gitignored; values are never logged.
- `redact_secrets(text)` replaces exact environment key values and
  common key shapes (`AIza...`, `sk-or-v1-...`, `Bearer ...`,
  `api_key=...`).
- Every error raised by this layer passes its message through the
  redactor. Never put a key in `meta`, a prompt, a URL, or a log line.
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
- Every embedding batch emits a `CostEvent` with `shadow_cost_usd = 0`,
  `in_tokens ≈ characters / 4`, `out_tokens = 0`, and meta
  `{"texts": N, "dim": 384}`.
- `FakeEmbedder` is deterministic: SHAKE-256 of the UTF-8 text expanded
  to 384 floats, L2-normalized. Same text in -> same vector out across
  runs and platforms. No network.

## 7. Fakes

`FakeLLM(responses, usage=None, model="fake-llm", meter=None)`:

- `responses` is a queue of `dict`, JSON `str`, `LLMResult`, `Exception`,
  or callable.
- Every call is recorded as
  `FakeCall(system, user, response_schema, call_site, max_retries, meta)`.
- Use `fake.calls` to assert prompt labeling, for example that untrusted
  source text was wrapped in `<untrusted_source>...</untrusted_source>`.
- Exhaustion raises `AssertionError`; scripted exceptions are raised.
- If a meter is supplied, fake calls emit zero-cost events.

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

`costs` reads the ledger and prints a fixed-width table with one row per
`call_site`: calls, in_tokens, out_tokens, shadow_cost_usd, plus TOTAL.
Missing ledger prints `No cost events at ...` and exits 0.

Environment:

- `WIKIMIND_LIVE=1` enables live smoke.
- `WIKIMIND_COSTS_PATH` overrides the default `costs.jsonl`.
- `WIKIMIND_ENV_FILE` overrides the `.env` path loaded by the CLI.

## 9. Integration rules for LANES 3-5

1. Construct one `CostMeter` per process/run (or share one); pass it to
   every real client. Real clients require a meter.
2. Use a canonical `call_site`; put arm tags and model versions in
   `meta`.
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
