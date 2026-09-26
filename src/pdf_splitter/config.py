from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MB = 1024 * 1024
# Under the jobs dir: where the api spools an upload's multipart part (upload.py `spooling_to`). The janitor never
# treats it as a job directory.
SPOOL = ".spool"


class Settings(BaseSettings):
    """Runtime settings, read from `PDFSPLIT_*` environment variables."""

    model_config = SettingsConfigDict(env_prefix="PDFSPLIT_")

    # Dev-local default; production mounts the shared volume at /jobs (Architecture § File layout).
    jobs_dir: Path = Path("jobs")
    max_bytes: int = 200 * MB
    max_pages: int = 2000
    ttl_hours: int = 24
    workers: int = 2
    rate_per_hour: int = 6
    # Uploads are refused (503) while the jobs volume has less than this free (Architecture § janitor).
    min_free_gb: float = 2
    # The peers whose X-Forwarded-For is believed, comma-separated (Caddy's IPv4 AND IPv6 address on the compose
    # network, STORY-013: it may reach the api over either); unset, the peer IS the client.
    trusted_proxy: str | None = None
    # Uploads the api streams at once; beyond it `POST /api/jobs` is 503 `overloaded` before a byte is read, so a
    # burst holds at most this many spool files open (upload.py `UploadGuard`).
    max_uploads: int = 4
    # uvicorn's own ceiling on open connections (503 beyond it): polls and downloads count too.
    limit_concurrency: int = 64
    # Of `max_uploads`, how many one client may hold at once (429, no slot spent; upload.py `UploadGuard`): a
    # trickle from one address can then pin at most half the slots, never all of them (gate r2). How long a
    # body may take is not the api's business (gate r3): Caddy's read_body bound (deploy/Caddyfile) cuts a
    # stalled one, and the api reads whatever arrives without ever cancelling a read.
    max_uploads_per_client: int = 2
    # Every other body (a JSON plan) is read whole before the route sees it (body_guard.py): at most this many
    # bytes (413) and all of it within `body_timeout` seconds (408), so a held-open PUT costs one connection for
    # seconds, not for as long as the client likes — and one client holds at most `max_bodies_per_client` of them
    # at once (429), so re-opening them as they time out can't keep `limit_concurrency` full (gate r3).
    max_json_bytes: int = 4 * MB
    body_timeout: float = 20
    max_bodies_per_client: int = 8
    # The secret under the daily-rotating IP hash (ADR-007). Unset, each api process draws its own at start, which
    # is fine for the one uvicorn process the CLI runs; several processes must share one so the window is shared.
    ip_salt: str | None = None
    # The ceiling on what one cut may write; the per-job budget is this or ~10× the upload, whichever is smaller,
    # and never more than the task's RLIMIT_FSIZE less room for the ZIP (worker/cut.py `OUTPUT_CEILING`), so a
    # setting this high is the sandbox's 1 GiB − 64 MiB in practice.
    max_output_bytes: int = 2 * 1024 * MB
    analyze_timeout: int = 300
    cut_timeout: int = 600
    public_url: str = "http://localhost:8000"

    @field_validator("jobs_dir", mode="after")
    @classmethod
    def _absolute_jobs_dir(cls, v: Path) -> Path:
        # Job paths are handed to subprocesses as argv; a relative jobs_dir (`.`, empty) would let an id
        # starting with `-` reach them looking like an option, and it would also drift with the cwd.
        return v.resolve()

    @property
    def db_path(self) -> Path:
        return self.jobs_dir / "jobs.db"

    @property
    def spool_dir(self) -> Path:
        return self.jobs_dir / SPOOL
