from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request

from .config import Settings
from .store import Store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(request: Request) -> Iterator[Store]:
    # One connection per request, so requests on different threadpool threads never share one.
    # FastAPI may run this teardown on a different thread than the endpoint; Store allows that.
    store = Store(request.app.state.settings.db_path)
    try:
        yield store
    finally:
        store.close()


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[Store, Depends(get_store)]
