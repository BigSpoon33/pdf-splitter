from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pdf_splitter.config import Settings
from pdf_splitter.store import Store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(jobs_dir=tmp_path / "jobs")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "jobs.db")
    s.init()
    yield s
    s.close()
