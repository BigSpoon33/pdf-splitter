from __future__ import annotations

import threading
from pathlib import Path

import monograph_splitter
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pdf_splitter import cli
from pdf_splitter.app import create_app
from pdf_splitter.config import Settings
from pdf_splitter.store import Store


def test_health_shape_and_jobs_dir_created(settings: Settings) -> None:
    assert not settings.jobs_dir.exists()
    with TestClient(create_app(settings)) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"ok", "queue", "disk_free_gb", "engine_version"}
    assert body["ok"] is True
    assert body["queue"] == 0
    assert isinstance(body["disk_free_gb"], float) and body["disk_free_gb"] >= 0
    assert body["engine_version"] == monograph_splitter.__version__ == "0.4.2"
    assert settings.db_path.exists()


def test_health_queue_counts_only_queued_jobs(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        store = Store(settings.db_path)
        for _ in range(3):
            store.create_job(ip_hash="h", filename="a.pdf", bytes=1, pages=1, ttl_hours=24)
        store.claim_next("analyze")
        store.close()
        assert client.get("/api/health").json()["queue"] == 2


def test_concurrent_health_requests_all_succeed(settings: Settings) -> None:
    # Sync endpoints and their dependency teardowns run on threadpool threads; many requests
    # at once is what exposed the cross-thread sqlite close (STORY-004 gate r1).
    n_threads, per_thread = 16, 25
    barrier = threading.Barrier(n_threads)
    statuses: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    with TestClient(create_app(settings), raise_server_exceptions=True) as client:

        def hammer() -> None:
            try:
                barrier.wait()
                for _ in range(per_thread):
                    r = client.get("/api/health")
                    with lock:
                        statuses.append(r.status_code)
            except BaseException as e:  # noqa: BLE001 - surfaced on the main thread
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not errors
    assert len(statuses) == n_threads * per_thread
    assert set(statuses) == {200}


def test_cli_api_serves_the_app_from_env_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = {}
    monkeypatch.setenv("PDFSPLIT_JOBS_DIR", str(tmp_path / "j"))
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    cli.main(["api"])
    assert isinstance(calls["app"], FastAPI)
    assert calls["app"].state.settings.jobs_dir == tmp_path / "j"
    assert (calls["host"], calls["port"]) == ("127.0.0.1", 8000)
    # uvicorn's own access log would print raw job ids; the app's redacting middleware replaces it.
    assert calls["access_log"] is False
    # uvicorn must not rewrite the peer from X-Forwarded-For on its own: ratelimit.client_ip's trusted-proxy
    # rule is the only XFF logic (STORY-013 addendum), so a spoofed header reaching the api port stays the peer's.
    assert calls["proxy_headers"] is False
