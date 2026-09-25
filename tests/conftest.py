from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fixtures.books import headed_book

from pdf_splitter.config import Settings
from pdf_splitter.files import write_json
from pdf_splitter.store import Store
from pdf_splitter.worker.analyze import analyze, default_plan


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(jobs_dir=tmp_path / "jobs")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    s = Store(tmp_path / "jobs.db")
    s.init()
    yield s
    s.close()


@pytest.fixture(scope="session")
def analyzed_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The synthetic book (headings source: exactly 3 chapters) analyzed once, in the shape the analyze task
    leaves a job dir: source.pdf, analysis.json, plan.json and the engine index. `helpers.seed_job` copies it."""
    root = tmp_path_factory.mktemp("analyzed")
    headed_book(root / "source.pdf", outline=False)
    analysis = analyze(root / "source.pdf", root / "work")
    write_json(root / "analysis.json", analysis)
    write_json(root / "plan.json", default_plan(analysis))
    return root
