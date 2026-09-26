# syntax=docker/dockerfile:1
# Three targets from one file (Architecture ADR-006/ADR-008): `web` builds the SPA, `python` is the api AND the
# worker image (same code, different `command:` in deploy/compose.yaml), `caddy` serves the SPA and proxies /api.

# ── web: the static SPA ───────────────────────────────────────────────────────────────────────────────
FROM oven/bun:1.3.5 AS web
WORKDIR /web
# The lockfile first, so a source-only change reuses the installed node_modules layer.
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web/ ./
RUN bun run build

# ── python-build: resolve the locked dependency set (the engine is a git dependency on public GitHub) ─
FROM python:3.12-slim AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /bin/uv
# git only here: uv fetches monograph-splitter from github.com; the runtime image below never needs it.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# uv.lock is copied as-is and honoured with --frozen: the image installs exactly what the tests ran against.
# README.md is the project's `readme` metadata, which hatchling insists on when it builds the wheel.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src/ src/
# --no-editable: the package lands in site-packages, so the runtime image only needs the venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ── python: the api + worker runtime ──────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS python
# A fixed uid: the `jobs` volume is initialised from this directory's ownership, so both containers (same
# image, same uid) can write to it and a read-only root filesystem is enough for everything else.
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir /jobs && chown app:app /jobs
COPY --from=python-build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PDFSPLIT_JOBS_DIR=/jobs
USER app
WORKDIR /app
ENTRYPOINT ["pdf-splitter"]
CMD ["api", "--host", "0.0.0.0", "--port", "8000"]

# ── caddy: TLS + static SPA + /api proxy ──────────────────────────────────────────────────────────────
FROM caddy:2-alpine AS caddy
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=web /web/dist /srv
