from __future__ import annotations

from pathlib import Path

import pytest

from pdf_splitter.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("JOBS_DIR", "MAX_BYTES", "MAX_PAGES", "TTL_HOURS", "WORKERS", "RATE_PER_HOUR", "MIN_FREE_GB",
                 "TRUSTED_PROXY", "IP_SALT", "MAX_OUTPUT_BYTES", "ANALYZE_TIMEOUT", "CUT_TIMEOUT", "PUBLIC_URL",
                 "MAX_UPLOADS", "LIMIT_CONCURRENCY", "MAX_UPLOADS_PER_CLIENT", "MIN_UPLOAD_RATE", "MAX_JSON_BYTES",
                 "BODY_TIMEOUT"):
        monkeypatch.delenv(f"PDFSPLIT_{name}", raising=False)
    s = Settings()
    assert s.jobs_dir == Path.cwd() / "jobs"
    assert s.max_bytes == 200 * 1024 * 1024
    assert s.max_pages == 2000
    assert s.ttl_hours == 24
    assert s.workers == 2
    assert s.rate_per_hour == 6
    # STORY-012 (PRD Q-3: the caps stand): 2 GB free, no proxy, a per-process salt, 2 GB of output per cut.
    assert s.min_free_gb == 2
    assert s.trusted_proxy is None and s.ip_salt is None
    assert s.max_output_bytes == 2 * 1024 * 1024 * 1024
    assert s.analyze_timeout == 300
    assert s.cut_timeout == 600
    assert s.public_url == "http://localhost:8000"
    assert s.db_path == Path.cwd() / "jobs" / "jobs.db"
    # STORY-013 gate r1: four uploads streaming at once, 64 open connections, the spool beside the jobs.
    assert (s.max_uploads, s.limit_concurrency) == (4, 64)
    assert s.spool_dir == Path.cwd() / "jobs" / ".spool"
    # Gate r2: one client holds at most two of the four slots; a body must keep moving (32 KiB per 30 s window
    # for an upload; a JSON body of at most 4 MiB, whole, within 20 s).
    assert (s.max_uploads_per_client, s.min_upload_rate) == (2, 32 * 1024)
    assert (s.max_json_bytes, s.body_timeout) == (4 * 1024 * 1024, 20)


@pytest.mark.parametrize("raw", [".", "", "jobs", "./jobs/../jobs"])
def test_jobs_dir_is_always_absolute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str) -> None:
    monkeypatch.chdir(tmp_path)
    for s in (Settings(jobs_dir=Path(raw)), Settings(jobs_dir=raw)):
        assert s.jobs_dir.is_absolute()
        assert s.jobs_dir == (tmp_path / raw).resolve()
        assert s.db_path.is_absolute()
    monkeypatch.setenv("PDFSPLIT_JOBS_DIR", raw)
    assert Settings().jobs_dir == (tmp_path / raw).resolve()


def test_env_prefix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = {
        "JOBS_DIR": str(tmp_path),
        "MAX_BYTES": "1000",
        "MAX_PAGES": "10",
        "TTL_HOURS": "1",
        "WORKERS": "4",
        "RATE_PER_HOUR": "60",
        "MIN_FREE_GB": "0.5",
        "TRUSTED_PROXY": "172.18.0.2",
        "IP_SALT": "shared-secret",
        "MAX_OUTPUT_BYTES": "300000",
        "ANALYZE_TIMEOUT": "30",
        "CUT_TIMEOUT": "60",
        "PUBLIC_URL": "https://split.example",
        "MAX_UPLOADS": "2",
        "LIMIT_CONCURRENCY": "16",
        "MAX_UPLOADS_PER_CLIENT": "1",
        "MIN_UPLOAD_RATE": "1024",
        "MAX_JSON_BYTES": "65536",
        "BODY_TIMEOUT": "2.5",
    }
    for k, v in env.items():
        monkeypatch.setenv(f"PDFSPLIT_{k}", v)
    s = Settings()
    assert s.jobs_dir == tmp_path
    assert (s.max_bytes, s.max_pages, s.ttl_hours, s.workers) == (1000, 10, 1, 4)
    assert (s.rate_per_hour, s.analyze_timeout, s.cut_timeout) == (60, 30, 60)
    assert (s.min_free_gb, s.trusted_proxy, s.ip_salt, s.max_output_bytes) == (0.5, "172.18.0.2", "shared-secret", 300000)
    assert s.public_url == "https://split.example"
    assert (s.max_uploads, s.limit_concurrency) == (2, 16)
    assert (s.max_uploads_per_client, s.min_upload_rate, s.max_json_bytes, s.body_timeout) == (1, 1024, 65536, 2.5)


def test_unprefixed_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDFSPLIT_MAX_PAGES", raising=False)
    monkeypatch.setenv("MAX_PAGES", "5")
    assert Settings().max_pages == 2000
