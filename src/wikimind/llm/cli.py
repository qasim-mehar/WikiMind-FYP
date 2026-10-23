"""Typer CLI: ``wikimind llm smoke`` and ``wikimind llm costs``."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from ..prices import EMBED_DIM, EMBED_MODEL, GEMINI_MODEL, JUDGE_MODEL
from .cost import (
    CostMeter,
    JsonlCostSink,
    default_costs_path,
    format_cost_report,
    format_usd,
    read_cost_events,
)
from .embedder import SentenceTransformerEmbedder
from .errors import CostSinkError
from .gemini import GeminiClient
from .judge import JudgeClient
from .secrets import (
    GEMINI_API_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
    gemini_api_key,
    load_dotenv,
    openrouter_api_key,
)

app = typer.Typer(no_args_is_help=True, help="WikiMind (Compendium) CLI.")
llm_app = typer.Typer(no_args_is_help=True, help="Model access layer.")
app.add_typer(llm_app, name="llm")

GEMINI_SMOKE_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "summary": {"type": "string"},
    },
    "required": ["ok", "summary"],
    "additionalProperties": False,
}

JUDGE_SMOKE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 5},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"],
    "additionalProperties": False,
}


def _dry_run_message(path: Path) -> str:
    return (
        "WIKIMIND_LIVE=1 is not set (or keys are missing); dry run, no network calls.\n"
        f"Would call Gemini {GEMINI_MODEL} with JSON mode (call_site qa.smoke).\n"
        f"Would call OpenRouter judge {JUDGE_MODEL} with JSON mode (call_site judge.smoke).\n"
        f"Would embed 3 texts with {EMBED_MODEL} -> {EMBED_DIM}-dim vectors "
        "(call_site embed.smoke).\n"
        f"Cost events would append to {path}.\n"
        "Set WIKIMIND_LIVE=1 and provide GEMINI_API_KEY / OPENROUTER_API_KEY to run."
    )


@llm_app.command("smoke")
def smoke(
    costs_path: Path | None = typer.Option(  # noqa: B008
        None,
        "--costs-path",
        help="JSONL cost ledger path (default: costs.jsonl or WIKIMIND_COSTS_PATH).",
    ),
) -> None:
    """One Gemini call, one judge call, one embedding, then the session cost report."""

    load_dotenv()
    path = costs_path or default_costs_path()
    live = os.environ.get("WIKIMIND_LIVE") == "1"
    gemini_key = gemini_api_key()
    openrouter_key = openrouter_api_key()

    if not live or gemini_key is None or openrouter_key is None:
        if live:
            missing = [
                name
                for name, value in (
                    (GEMINI_API_KEY_ENV, gemini_key),
                    (OPENROUTER_API_KEY_ENV, openrouter_key),
                )
                if value is None
            ]
            typer.echo(f"Missing keys for live smoke: {', '.join(missing)}")
        typer.echo(_dry_run_message(path))
        raise typer.Exit(code=0)

    meter = CostMeter(JsonlCostSink(path))
    failures: list[tuple[str, str]] = []

    try:
        gemini = GeminiClient(api_key=gemini_key, meter=meter)
        gemini_result = gemini.complete(
            system=(
                "You are the WikiMind model-access smoke test. "
                "Return only the requested JSON object."
            ),
            user='Return {"ok": true, "summary": "gemini smoke ok"}.',
            response_schema=GEMINI_SMOKE_SCHEMA,
            call_site="qa.smoke",
            max_retries=3,
            meta={"arm": "smoke", "model_version": GEMINI_MODEL},
        )
        typer.echo(f"Gemini: {json.dumps(gemini_result.json, sort_keys=True)}")
    except Exception as exc:  # noqa: BLE001
        failures.append(("gemini", str(exc)))

    try:
        judge = JudgeClient(api_key=openrouter_key, meter=meter)
        judge_result = judge.complete(
            system=(
                "You are a strict, blind judge. Score the supplied statement "
                "from 1 to 5 and explain briefly."
            ),
            user='Score this statement: "WikiMind smoke tests the model layer."',
            response_schema=JUDGE_SMOKE_SCHEMA,
            call_site="judge.smoke",
            max_retries=3,
            meta={"arm": "smoke"},
        )
        typer.echo(f"Judge: {json.dumps(judge_result.json, sort_keys=True)}")
    except Exception as exc:  # noqa: BLE001
        failures.append(("judge", str(exc)))

    try:
        embedder = SentenceTransformerEmbedder(meter=meter, call_site="embed.smoke")
        vectors = embedder.embed(
            ["WikiMind smoke test.", "Source-tracked knowledge graph.", "Compendium."]
        )
        typer.echo(
            f"Embedder: {len(vectors)} vectors, dim={len(vectors[0])}, "
            f"norm0={sum(v * v for v in vectors[0]) ** 0.5:.6f}"
        )
    except Exception as exc:  # noqa: BLE001
        failures.append(("embed", str(exc)))

    typer.echo("")
    typer.echo("Cost report (this session):")
    typer.echo(format_cost_report(meter.events))
    typer.echo(
        f"Total session shadow cost: {format_usd(meter.total_shadow_cost_usd)}"
    )
    typer.echo(f"Cost ledger: {path}")

    if failures:
        for name, message in failures:
            typer.echo(f"{name} failed: {message}", err=True)
        raise typer.Exit(code=1)


@llm_app.command("costs")
def costs(
    path: Path | None = typer.Option(  # noqa: B008
        None,
        "--path",
        "-p",
        help="JSONL cost ledger path (default: costs.jsonl or WIKIMIND_COSTS_PATH).",
    ),
) -> None:
    """Aggregate the JSONL cost ledger by call_site."""

    ledger = path or default_costs_path()
    try:
        events = read_cost_events(ledger)
    except CostSinkError as exc:
        typer.echo(f"Could not read cost ledger: {exc}", err=True)
        raise typer.Exit(code=1) from None
    if not events:
        typer.echo(f"No cost events at {ledger}.")
        raise typer.Exit(code=0)
    typer.echo(format_cost_report(events))


if __name__ == "__main__":  # pragma: no cover
    app()
