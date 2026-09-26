from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import monograph_splitter
from fastapi import FastAPI

from . import errors
from .access_log import access_log
from .body_guard import BodyGuard
from .config import Settings
from .deps import SettingsDep, StoreDep, get_settings, get_store
from .routes import download, plan, preview
from .store import Store
from .upload import UploadGuard, spooling_to
from .upload import router as upload_router

__all__ = ["SettingsDep", "StoreDep", "create_app", "get_settings", "get_store"]


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
        with spooling_to(settings.spool_dir):
            yield

    app = FastAPI(title="pdf-splitter", lifespan=lifespan)
    app.state.settings = settings
    # Added first, so the access log wraps them: a refusal a guard sends without reading the body is still
    # logged, with an X-Request-ID. The two guards split the requests by path (the upload; every other body).
    app.add_middleware(BodyGuard, settings=settings)
    app.add_middleware(UploadGuard, settings=settings)
    app.middleware("http")(access_log)
    errors.install(app)
    for router in (upload_router, plan.router, preview.router, download.router):
        app.include_router(router)

    @app.get("/api/health")
    def health(settings: SettingsDep, store: StoreDep) -> dict:
        return {
            "ok": True,
            "queue": store.queue_length(),
            "disk_free_gb": round(shutil.disk_usage(settings.jobs_dir).free / 1e9, 1),
            "engine_version": monograph_splitter.__version__,
        }

    return app
