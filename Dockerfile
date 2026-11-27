# syntax=docker/dockerfile:1
# Local/demo image for the Lane 17 web prototype. NOT a public deployment:
# the image itself has no authentication and Compose publishes only to loopback.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ---------------------------------------------------------------------------
# Dependency layer. pyproject.toml is the source of truth for the pinned
# runtime stack (Python >=3.11,<3.12, psycopg, pypdf<7, google-genai via the
# `llm` extra). Copy the packaging metadata first so a code-only rebuild can
# reuse this layer.
#
# `fastapi` and `uvicorn` are imported by src/wikimind/webapp/ but are NOT
# declared in any pyproject extra (README.dev.md says they are in `dev`, but
# they are not). They are installed explicitly here because editing
# pyproject.toml is outside this lane's write scope. No src/ change is made.
#
# The `embed` extra (sentence-transformers / torch) is intentionally omitted:
# the webapp falls back to its deterministic FakeEmbedder, so the demo needs no
# model download and no HuggingFace network access. See docker/README.md.
# ---------------------------------------------------------------------------
COPY pyproject.toml README.dev.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir ".[llm]" \
    "fastapi>=0.100" "uvicorn>=0.27"

# Runtime files: migrations are applied by docker/entrypoint.sh on first boot.
COPY migrations ./migrations
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && groupadd --gid 10001 wikimind \
    && useradd --uid 10001 --gid wikimind --create-home --shell /bin/sh wikimind \
    && mkdir -p /app/data/webapp/uploads \
    && chown -R wikimind:wikimind /app

# Never run the demo server as root.
USER wikimind
ENV HOME=/home/wikimind

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "wikimind.webapp"]
