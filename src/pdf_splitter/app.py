from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Annotated

import monograph_splitter
from fastapi import Depends, FastAPI, Request

from .config import Settings
from .store import Store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> Iterator[Store]:
    # One connection per request: sqlite3 connections are bound to their creating thread, and
    # sync endpoints run on a threadpool.
    store = Store(request.app.state.settings.db_path)
    try:
        yield store
    finally:
        store.close()


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[Store, Depends(get_store)]


def create_app(settings: Settings | None = None) -> FastAPI:
    # Settings are injected rather than read at import time, so tests never touch the real JOBS_DIR.
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        store = Store(settings.db_path)
        try:
            store.init()
        finally:
            store.close()
        yield

    app = FastAPI(title="pdf-splitter", lifespan=lifespan)
    app.state.settings = settings

    @app.get("/api/health")
    def health(settings: SettingsDep, store: StoreDep) -> dict:
        return {
            "ok": True,
            "queue": store.queue_length(),
            "disk_free_gb": round(shutil.disk_usage(settings.jobs_dir).free / 1e9, 1),
            "engine_version": monograph_splitter.__version__,
        }

    return app
