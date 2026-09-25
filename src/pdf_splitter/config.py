from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

MB = 1024 * 1024


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
    analyze_timeout: int = 300
    cut_timeout: int = 600
    public_url: str = "http://localhost:8000"

    @property
    def db_path(self) -> Path:
        return self.jobs_dir / "jobs.db"
