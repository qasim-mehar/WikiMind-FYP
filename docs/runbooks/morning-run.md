# Standing Order — "MORNING RUN"

## Definition

**MORNING RUN** is a trigger word. When the user sends a message that is
essentially just `MORNING RUN` (case-insensitive), execute the daily
cap-bounded compile resume of the v0-fixtures corpus. Nothing else ever
triggers it.

## Context

OpenRouter's free route allows ~50 judge (Nemotron `:free`) calls per day,
refilling 00:05 UTC (05:05 PKT). The corpus compile (229 units total, 11 done
at last report) therefore runs in daily slices sized to that cap.

There is **NO background waiter** — the user is the alarm clock, by design.

## The Command

Git Bash, from the main worktree:

```bash
cd /c/Users/mianq/Projects/WikiMind
WIKIMIND_LIVE=1 \
WIKIMIND_DB="$(grep '^WIKIMIND_DB=' .env | cut -d= -f2- | sed 's|/wikimind_l0$|/wikimind_live_l3|')" \
./.venv/Scripts/wikimind.exe compile v0-fixtures --delay 3
```

`.env` stays pointed at `wikimind_l0`; the override targets the corpus DB
`wikimind_live_l3` — never edit `.env` for this.

## Rules

- Execute ONLY on the trigger (case-insensitive). Never spend judge calls
  outside a morning run.
- Precondition: Postgres running; no merge/ritual actively in progress in
  this worktree.
- The run halts by itself on HTTP 429 (daily cap) — expected, not an error.
  Completed units short-circuit; nothing is ever paid for twice.
- FORBIDDEN: `wikimind db reset --confirm` against any database; any
  drop/write outside the compile path.
- After the run, report: units done (x/229), judge calls used this run,
  running shadow total, actual spend (expected $0).
- When a report reaches 229/229: stop, and tell the user to return to the
  governance discussion for the Gate B package.
