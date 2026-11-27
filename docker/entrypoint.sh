#!/bin/sh
# One-shot bootstrap for the demo stack: wait for Postgres, apply migrations,
# ensure the sandbox project exists, then hand off to the webapp.
# Runs as the non-root `wikimind` user created in the Dockerfile.
set -eu

DB_DSN="${WIKIMIND_DB:-${WIKIMIND_WEB_DB:-}}"
if [ -z "$DB_DSN" ]; then
    echo "error: set WIKIMIND_DB (or WIKIMIND_WEB_DB) in .env" >&2
    exit 2
fi

# Belt-and-suspenders guard: the local interface must never query the
# protected corpus. src/wikimind/webapp/config.py enforces the same rule, but
# the CLI bootstrap commands run before the webapp config is built.
case "$(printf '%s' "$DB_DSN" | tr '[:upper:]' '[:lower:]')" in
    *wikimind_live_l3*)
        echo "error: the demo stack refuses the protected corpus wikimind_live_l3" >&2
        exit 2
        ;;
esac

# The DB cost mirror is opt-in and must never be armed by the demo stack.
if [ -n "${WIKIMIND_COST_DB_MIRROR:-}" ]; then
    echo "error: WIKIMIND_COST_DB_MIRROR must not be set in the demo stack" >&2
    exit 2
fi

echo "wikimind: applying migrations to the sandbox database"
wikimind db migrate --migrations-dir /app/migrations

PROJECT="${WIKIMIND_WEB_PROJECT:-webapp-sandbox}"
if ! wikimind project list | grep -q "[[:space:]]${PROJECT}\$"; then
    echo "wikimind: creating project ${PROJECT}"
    wikimind project create "$PROJECT"
else
    echo "wikimind: project ${PROJECT} already present"
fi

echo "wikimind: starting ${*} on ${WIKIMIND_WEB_HOST:-0.0.0.0}:${WIKIMIND_WEB_PORT:-8000}"
exec "$@"
