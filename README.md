# pdf-splitter

A public website on top of [pdf-splitter-engine](https://github.com/BigSpoon33/pdf-splitter-engine) (Python package `monograph-splitter`): drag in a large text-layer PDF, choose how sections are found (PDF outline, big headings, or a pasted list), tune the layout (columns, bands), preview and drag the cuts, and download one PDF per chapter/entry.

Planning: [`docs/PRD.md`](docs/PRD.md) · [`docs/Architecture.md`](docs/Architecture.md) · [`docs/stories/`](docs/stories/)

## Development

Python 3.12 and [uv](https://docs.astral.sh/uv/). The engine is installed from its public GitHub tag (`pyproject.toml`).

```bash
uv sync                      # create .venv, install deps + dev tools
uv run pytest                # tests
uv run ruff check            # lint
uv run pdf-splitter api      # API on http://127.0.0.1:8000 (--host / --port to change)
curl -s localhost:8000/api/health
curl -s -F file=@book.pdf localhost:8000/api/jobs   # 201 {id, state} or 4xx {code, message}
uv run pdf-splitter worker   # run queued jobs (same PDFSPLIT_JOBS_DIR as the api)
```

The worker claims queued jobs, at most `PDFSPLIT_WORKERS` at a time, and runs each as
`python -m pdf_splitter.task <kind> -- <id>` through `pdf_splitter.worker.sandbox` (RLIMIT_AS 2 GB, RLIMIT_CPU
timeout + 10 s, RLIMIT_FSIZE 1 GB) under the kind's wall-clock timeout. An analyze job writes
`<id>/analysis.json`, `<id>/plan.json` and the engine index `<id>/work/.book-index.json`, then moves the row to
`review`; a failure leaves `failed` with `error_code` `timeout`, `resources` or `internal`. SIGTERM stops
claiming and lets running jobs finish.

Request logs come from the app (`pdf_splitter.access`), not uvicorn, so job ids appear only as `log_id` hashes.

### Configuration

All settings are environment variables with the `PDFSPLIT_` prefix (`src/pdf_splitter/config.py`).

| Variable | Default | Meaning |
|----------|---------|---------|
| `PDFSPLIT_JOBS_DIR` | `./jobs` | uploads, outputs and `jobs.db` (production: `/jobs`) |
| `PDFSPLIT_MAX_BYTES` | `209715200` | largest upload, in bytes (200 MiB = 200 × 1024 × 1024) |
| `PDFSPLIT_MAX_PAGES` | `2000` | largest page count |
| `PDFSPLIT_TTL_HOURS` | `24` | hours a job and its files are kept |
| `PDFSPLIT_WORKERS` | `2` | concurrent analyze/cut jobs |
| `PDFSPLIT_RATE_PER_HOUR` | `6` | uploads per IP per hour |
| `PDFSPLIT_ANALYZE_TIMEOUT` | `300` | analyze job wall-clock limit, seconds |
| `PDFSPLIT_CUT_TIMEOUT` | `600` | cut job wall-clock limit, seconds |
| `PDFSPLIT_PUBLIC_URL` | `http://localhost:8000` | the site's public base URL |
