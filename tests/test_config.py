from __future__ import annotations

from pathlib import Path

import pytest

from pdf_splitter.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("JOBS_DIR", "MAX_BYTES", "MAX_PAGES", "TTL_HOURS", "WORKERS", "RATE_PER_HOUR",
                 "ANALYZE_TIMEOUT", "CUT_TIMEOUT", "PUBLIC_URL"):
        monkeypatch.delenv(f"PDFSPLIT_{name}", raising=False)
    s = Settings()
    assert s.jobs_dir == Path.cwd() / "jobs"
    assert s.max_bytes == 200 * 1024 * 1024
    assert s.max_pages == 2000
    assert s.ttl_hours == 24
    assert s.workers == 2
    assert s.rate_per_hour == 6
    assert s.analyze_timeout == 300
    assert s.cut_timeout == 600
    assert s.public_url == "http://localhost:8000"
    assert s.db_path == Path.cwd() / "jobs" / "jobs.db"


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
        "ANALYZE_TIMEOUT": "30",
        "CUT_TIMEOUT": "60",
        "PUBLIC_URL": "https://split.example",
    }
    for k, v in env.items():
        monkeypatch.setenv(f"PDFSPLIT_{k}", v)
    s = Settings()
    assert s.jobs_dir == tmp_path
    assert (s.max_bytes, s.max_pages, s.ttl_hours, s.workers) == (1000, 10, 1, 4)
    assert (s.rate_per_hour, s.analyze_timeout, s.cut_timeout) == (60, 30, 60)
    assert s.public_url == "https://split.example"


def test_unprefixed_env_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDFSPLIT_MAX_PAGES", raising=False)
    monkeypatch.setenv("MAX_PAGES", "5")
    assert Settings().max_pages == 2000
