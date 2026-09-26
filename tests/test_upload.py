from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from pdf_splitter import preflight, upload
from pdf_splitter.access_log import redact_path
from pdf_splitter.app import create_app
from pdf_splitter.config import Settings
from pdf_splitter.store import Store, log_id

TEXT = "The quick brown fox jumps over the lazy dog, chapter one begins here."


def pdf_bytes(pages: int = 1, text: str | None = TEXT, **save_kw) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = doc.tobytes(**save_kw)
    doc.close()
    return data


def image_only_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 64), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    page.insert_image(fitz.Rect(72, 72, 272, 272), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


def png_bytes() -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    return pix.tobytes("png")


def encrypted_pdf() -> bytes:
    return pdf_bytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")


def post(client: TestClient, data: bytes, name: str = "book.pdf", headers: dict[str, str] | None = None):
    return client.post("/api/jobs", files={"file": (name, io.BytesIO(data), "application/pdf")}, headers=headers)


def job_count(settings: Settings) -> int:
    store = Store(settings.db_path)
    try:
        return store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        store.close()


def job_dirs(settings: Settings) -> list[Path]:
    # The api's spool directory lives beside the jobs (config.SPOOL); only the rest are job directories.
    return [p for p in settings.jobs_dir.iterdir() if p.is_dir() and p != settings.spool_dir]


def assert_rejected(r, settings: Settings, status: int, code: str) -> None:
    assert r.status_code == status, r.text
    body = r.json()
    assert body == {"code": code, "message": upload.MESSAGES[code]}
    assert str(settings.jobs_dir) not in r.text
    assert job_dirs(settings) == []
    assert job_count(settings) == 0


# --- happy path -------------------------------------------------------------------------------


def test_upload_creates_queued_analyze_job(settings: Settings) -> None:
    data = pdf_bytes(pages=3)
    with TestClient(create_app(settings)) as client:
        r = post(client, data, name="../../etc/My Book.pdf")
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"id", "state"}
    assert body["state"] == "queued"
    job_id = body["id"]
    assert len(job_id) == 22 and all(c.isalnum() or c in "-_" for c in job_id)

    job_dir = settings.jobs_dir / job_id
    assert sorted(p.name for p in job_dir.iterdir()) == ["source.pdf"]
    assert (job_dir / "source.pdf").read_bytes() == data

    store = Store(settings.db_path)
    job = store.get_job(job_id)
    store.close()
    assert job is not None
    assert (job["state"], job["kind"]) == ("queued", "analyze")
    assert job["pages"] == 3
    assert job["bytes"] == len(data)
    assert job["filename"] == "My Book.pdf"
    assert len(job["ip_hash"]) == 64 and "testclient" not in job["ip_hash"]
    assert job["expires_at"] > job["created_at"]


def test_upload_exactly_at_the_cap_is_accepted(tmp_path: Path) -> None:
    data = pdf_bytes()
    settings = Settings(jobs_dir=tmp_path / "jobs", max_bytes=len(data))
    with TestClient(create_app(settings)) as client:
        assert post(client, data).status_code == 201


# --- rejections (AC-2, AC-4, AC-5) ------------------------------------------------------------


def test_too_large(tmp_path: Path) -> None:
    data = pdf_bytes()
    settings = Settings(jobs_dir=tmp_path / "jobs", max_bytes=len(data) - 1)
    with TestClient(create_app(settings)) as client:
        r = post(client, data)
    assert_rejected(r, settings, 413, "too_large")


def test_too_large_stops_copying_at_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Streaming, not buffering: the copy reads at most one chunk past the cap and then gives up.
    monkeypatch.setattr(upload, "CHUNK", 1024)
    settings = Settings(jobs_dir=tmp_path / "jobs", max_bytes=4096)
    reads: list[int] = []

    class Counting(io.BytesIO):
        def read(self, n: int = -1) -> bytes:
            chunk = super().read(n)
            reads.append(len(chunk))
            return chunk

    part = tmp_path / "x.part"
    fake = type("F", (), {"file": Counting(b"%PDF-" + b"0" * 100_000)})()
    assert upload._copy_capped(fake, part, settings.max_bytes) is None
    assert max(reads) == 1024
    assert sum(reads) <= settings.max_bytes + 1024
    assert part.stat().st_size <= settings.max_bytes


def test_not_pdf_png_renamed(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = post(client, png_bytes(), name="x.pdf")
    assert_rejected(r, settings, 400, "not_pdf")


def test_not_pdf_empty_and_missing_file(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert_rejected(post(client, b""), settings, 400, "not_pdf")
        assert_rejected(client.post("/api/jobs"), settings, 400, "not_pdf")


def test_encrypted(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = post(client, encrypted_pdf())
    assert_rejected(r, settings, 400, "encrypted")


def test_too_many_pages(tmp_path: Path) -> None:
    settings = Settings(jobs_dir=tmp_path / "jobs", max_pages=3)
    with TestClient(create_app(settings)) as client:
        r = post(client, pdf_bytes(pages=4))
    assert_rejected(r, settings, 413, "too_many_pages")


def test_no_text_layer_image_only(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = post(client, image_only_pdf())
    assert_rejected(r, settings, 400, "no_text_layer")


def test_unreadable_garbage_after_magic(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = post(client, b"%PDF-1.7\n" + b"\x00garbage" * 50)
    assert_rejected(r, settings, 400, "unreadable")


def test_unreadable_on_preflight_timeout(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(upload.subprocess, "run", hang)
    with TestClient(create_app(settings)) as client:
        r = post(client, pdf_bytes())
    assert_rejected(r, settings, 400, "unreadable")


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, b""), (-11, b""), (0, b"not json"), (0, b"[1, 2]"), (0, b'{"ok": false, "code": "../etc"}'), (0, b'{"ok": true}'),
     (0, b'{"ok": true, "pages": "3"}')],
)
def test_unreadable_on_preflight_crash_or_garbage(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: bytes
) -> None:
    monkeypatch.setattr(
        upload.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, returncode, stdout, b"boom")
    )
    with TestClient(create_app(settings)) as client:
        r = post(client, pdf_bytes())
    assert_rejected(r, settings, 400, "unreadable")


def test_preflight_uses_a_10s_timeout_and_this_interpreter(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def fake(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, b'{"ok": true, "pages": 1}', b"")

    monkeypatch.setattr(upload.subprocess, "run", fake)
    with TestClient(create_app(settings)) as client:
        assert post(client, pdf_bytes()).status_code == 201
    assert seen["timeout"] == 10.0
    assert seen["cmd"][:3] == [sys.executable, "-m", "pdf_splitter.preflight"]
    # `--` before the path: an id that starts with `-` must never parse as an option.
    assert seen["cmd"][-2] == "--"
    assert seen["cmd"][-1].startswith(str(settings.jobs_dir))
    assert Path(seen["cmd"][-1]).is_absolute()


def test_upload_with_relative_jobs_dir_and_dash_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """1 in 64 ids starts with `-`; with `PDFSPLIT_JOBS_DIR=.` the preflight path used to read as an option."""
    monkeypatch.chdir(tmp_path)
    ids = iter(["-bCdEfGhIjKlMnOpQrSt00", "--CdEfGhIjKlMnOpQrSt01"])
    monkeypatch.setattr(upload, "new_job_id", lambda: next(ids))
    settings = Settings(jobs_dir=Path("."))
    assert settings.jobs_dir == tmp_path.resolve()
    with TestClient(create_app(settings)) as client:
        for expected in ("-bCdEfGhIjKlMnOpQrSt00", "--CdEfGhIjKlMnOpQrSt01"):
            r = post(client, pdf_bytes())
            assert r.status_code == 201, r.json()
            assert r.json()["id"] == expected
            assert (tmp_path / expected / "source.pdf").exists()
    assert job_count(settings) == 2


def test_preflight_main_accepts_a_dash_leading_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    Path("-x.pdf").write_bytes(pdf_bytes())
    assert preflight.main(["--max-pages", "5", "--", "-x.pdf"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {"ok": True, "pages": 1}
    with pytest.raises(SystemExit):
        preflight.main(["--max-pages", "5", "-x.pdf"])


def test_row_insert_failure_leaves_no_directory(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(Store, "create_job", boom)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        r = post(client, pdf_bytes())
    assert r.status_code == 500
    assert job_dirs(settings) == []


# --- preflight module (AC-2) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "max_pages", "expected"),
    [
        (lambda: pdf_bytes(pages=2), 10, {"ok": True, "pages": 2}),
        (png_bytes, 10, {"ok": False, "code": "not_pdf"}),
        (encrypted_pdf, 10, {"ok": False, "code": "encrypted"}),
        (lambda: pdf_bytes(pages=4), 3, {"ok": False, "code": "too_many_pages", "pages": 4}),
        (image_only_pdf, 10, {"ok": False, "code": "no_text_layer", "pages": 1}),
        (lambda: b"%PDF-1.7\n" + b"\x00" * 64, 10, {"ok": False, "code": "unreadable"}),
    ],
)
def test_preflight_check(tmp_path: Path, data, max_pages: int, expected: dict) -> None:
    path = tmp_path / "in.pdf"
    path.write_bytes(data())
    assert preflight.check(path, max_pages) == expected


def test_preflight_order_encrypted_before_page_count(tmp_path: Path) -> None:
    path = tmp_path / "in.pdf"
    path.write_bytes(pdf_bytes(pages=4, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y"))
    assert preflight.check(path, 3)["code"] == "encrypted"


def test_preflight_text_probe_needs_50_chars_across_sampled_pages(tmp_path: Path) -> None:
    path = tmp_path / "in.pdf"
    path.write_bytes(pdf_bytes(pages=3, text="x" * 16))  # 48 chars total
    assert preflight.check(path, 10)["code"] == "no_text_layer"
    path.write_bytes(pdf_bytes(pages=3, text="x" * 17))  # 51
    assert preflight.check(path, 10) == {"ok": True, "pages": 3}


def test_probe_indices_are_evenly_spaced_and_capped() -> None:
    assert preflight.probe_indices(0) == []
    assert preflight.probe_indices(5) == [0, 1, 2, 3, 4]
    idx = preflight.probe_indices(2000)
    assert len(idx) == 12 and idx[0] == 0 and idx[-1] == 1999


def test_preflight_subprocess_prints_one_json_object(tmp_path: Path) -> None:
    path = tmp_path / "in.pdf"
    path.write_bytes(pdf_bytes(pages=2))
    proc = subprocess.run(
        [sys.executable, "-m", "pdf_splitter.preflight", "--max-pages", "5", str(path)],
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert json.loads(proc.stdout.splitlines()[-1]) == {"ok": True, "pages": 2}


# --- filename sanitising (AC-3) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("book.pdf", "book.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\Book One.pdf", "Book One.pdf"),
        ("a\x00b\x1f\x7fc\u202e.pdf", "abc.pdf"),
        ("", "document.pdf"),
        (None, "document.pdf"),
        ("dir/", "document.pdf"),
        ("..", "document.pdf"),
        ("  \t ", "document.pdf"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert upload.sanitize_filename(raw) == expected


def test_sanitize_filename_caps_at_120_keeping_the_extension() -> None:
    name = upload.sanitize_filename("x" * 300 + ".pdf")
    assert len(name) == 120 and name.endswith(".pdf")
    assert len(upload.sanitize_filename("y" * 300)) == 120


def test_uploaded_long_traversal_name_is_sanitized(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        r = post(client, pdf_bytes(), name="../" * 10 + "z" * 300 + ".pdf")
    store = Store(settings.db_path)
    job = store.get_job(r.json()["id"])
    store.close()
    assert job["filename"] == "z" * 116 + ".pdf"


# --- concurrency + logging --------------------------------------------------------------------


def test_concurrent_uploads_all_succeed(settings: Settings) -> None:
    n_threads, per_thread = 6, 3
    barrier = threading.Barrier(n_threads)
    ids: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    data = pdf_bytes(pages=2)
    # 18 uploads would trip the 6/h window (STORY-012): each thread is its own client behind the trusted proxy.
    # Six at once is more than the in-flight cap (STORY-013 gate r1) allows by default, so it is raised here.
    settings = settings.model_copy(update={"trusted_proxy": "testclient", "max_uploads": n_threads})

    with TestClient(create_app(settings), raise_server_exceptions=True) as client:

        def hammer(n: int) -> None:
            try:
                barrier.wait()
                for _ in range(per_thread):
                    r = post(client, data, headers={"X-Forwarded-For": f"203.0.113.{n}"})
                    assert r.status_code == 201, r.text
                    with lock:
                        ids.append(r.json()["id"])
            except BaseException as e:  # noqa: BLE001 - surfaced on the main thread
                errors.append(e)

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert client.get("/api/health").json()["queue"] == n_threads * per_thread

    assert not errors
    assert len(set(ids)) == n_threads * per_thread
    assert job_count(settings) == n_threads * per_thread
    assert {p.name for p in job_dirs(settings)} == set(ids)


# --- the guard (STORY-013 gate r1): refused before the body is read -----------------------------


def rate_rows(settings: Settings) -> int:
    store = Store(settings.db_path)
    try:
        return store.conn.execute("SELECT COUNT(*) FROM rate").fetchone()[0]
    finally:
        store.close()


def never_reached(*args, **kwargs):
    raise AssertionError("the route ran: the guard let the body through")


class Reading:
    """Around the whole app: counts the body messages and bytes the app took from `receive`. "Refused before the
    body is read" means both stay at zero — the spool directory could never show it, since Starlette's spool file
    is an unnamed `O_TMPFILE` (gate r2)."""

    def __init__(self, app) -> None:
        self.app, self.messages, self.bytes = app, 0, 0

    async def __call__(self, scope, receive, send) -> None:
        async def counting():
            message = await receive()
            if message["type"] == "http.request":
                self.messages += 1
                self.bytes += len(message.get("body", b""))
            return message

        await self.app(scope, counting, send)


def reading_client(settings: Settings) -> tuple[TestClient, Reading]:
    reading = Reading(create_app(settings))
    return TestClient(reading), reading


def assert_unread(reading: Reading) -> None:
    assert (reading.messages, reading.bytes) == (0, 0), f"the app read {reading.bytes} bytes of body"


def test_oversized_content_length_is_refused_before_the_body_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared size past the cap (plus the multipart envelope) is 413 from the headers alone: the route never
    runs, not a byte of body is taken from the socket, no slot is spent, and the connection is told to close so
    the client stops sending."""
    settings = Settings(jobs_dir=tmp_path / "jobs", max_bytes=1000)
    monkeypatch.setattr(upload, "_accept", never_reached)
    client, reading = reading_client(settings)
    with client:
        # 2 MiB: past Starlette's 1 MiB in-memory spool, so a parsed body would have hit the temporary directory.
        r = post(client, b"%PDF-" + b"0" * (2 * 1024 * 1024))
        assert_rejected(r, settings, 413, "too_large")
        assert r.headers["connection"] == "close"
        assert_unread(reading)
    assert rate_rows(settings) == 0
    # Just above the cap but inside the envelope allowance: the guard passes it, the streaming copy decides.
    monkeypatch.setattr(upload, "_accept", lambda *a, **k: upload.reject("too_large"))
    with TestClient(create_app(settings)) as client:
        assert post(client, b"%PDF-" + b"0" * 1000).status_code == 413


def test_uploads_without_a_content_length_or_not_multipart_are_refused_unread(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload, "_accept", never_reached)
    with TestClient(create_app(settings)) as client:
        # httpx sends an iterator body chunked, without Content-Length.
        r = client.post("/api/jobs", content=iter([b"--x\r\n", b"--x--\r\n"]),
                        headers={"Content-Type": "multipart/form-data; boundary=x"})
        assert (r.status_code, r.json()["code"]) == (411, "invalid"), r.text
        # The urlencoded parser would hold the whole body in memory; only multipart streams.
        r = client.post("/api/jobs", data={"mode": "chapters"})
        assert (r.status_code, r.json()["code"]) == (415, "invalid"), r.text
        r = client.post("/api/jobs", content=b"x" * 10, headers={"Content-Type": "application/pdf"})
        assert r.status_code == 415
        # Other methods and paths are none of the guard's business.
        assert client.get("/api/jobs").status_code == 405
        assert client.get("/api/health").status_code == 200
    assert rate_rows(settings) == 0
    # No body at all has nothing to spool: the route keeps answering it (test_not_pdf_empty_and_missing_file).
    monkeypatch.setattr(upload, "_accept", lambda *a, **k: upload.reject("not_pdf"))
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/jobs").status_code == 400
    assert rate_rows(settings) == 1


def test_the_rate_slot_is_taken_before_the_body_is_read_and_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(jobs_dir=tmp_path / "jobs", rate_per_hour=1)
    client, reading = reading_client(settings)
    with client:
        data = pdf_bytes()
        assert post(client, data).status_code == 201
        # One upload, one slot: the route did not claim a second one on top of the guard's. The accepted upload
        # is what the counter sees: the whole multipart body, so a zero afterwards means something.
        assert rate_rows(settings) == 1
        assert reading.messages >= 1 and reading.bytes > len(data)
        reading.messages = reading.bytes = 0
        monkeypatch.setattr(upload, "_accept", never_reached)
        r = post(client, pdf_bytes())
        assert r.status_code == 429, r.text
        assert 3500 < int(r.headers["retry-after"]) <= 3600
        assert r.headers["connection"] == "close"
        assert_unread(reading)
    assert rate_rows(settings) == 1 and job_count(settings) == 1


def test_the_reading_counter_catches_a_guard_that_drains_the_body_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The instrument behind the two tests above, proven: a guard that took the body before refusing (what the
    spool-directory check could not see) moves the counter, and `assert_unread` fails."""
    settings = Settings(jobs_dir=tmp_path / "jobs", max_bytes=1000)
    real = upload.UploadGuard.__call__

    async def draining(self, scope, receive, send):
        while scope["type"] == "http" and (await receive()).get("more_body"):
            pass
        await real(self, scope, receive, send)

    monkeypatch.setattr(upload.UploadGuard, "__call__", draining)
    monkeypatch.setattr(upload, "_accept", never_reached)
    client, reading = reading_client(settings)
    with client:
        assert post(client, b"%PDF-" + b"0" * (2 * 1024 * 1024)).status_code == 413
    assert reading.messages == 1 and reading.bytes > 2 * 1024 * 1024
    with pytest.raises(AssertionError, match="read .* bytes of body"):
        assert_unread(reading)


def test_uploads_beyond_the_in_flight_cap_are_503_overloaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two uploads held inside the route (a slow preflight), a third arrives: 503 `overloaded` with a Retry-After,
    no slot spent on it; once the two finish, the cap is free again."""
    settings = Settings(jobs_dir=tmp_path / "jobs", max_uploads=2, trusted_proxy="testclient")
    entered, release = threading.Semaphore(0), threading.Event()
    real = upload.run_preflight

    def slow_preflight(*args, **kwargs):
        entered.release()
        assert release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(upload, "run_preflight", slow_preflight)
    statuses: list[int] = []
    with TestClient(create_app(settings)) as client:

        def attempt(n: int) -> None:
            statuses.append(post(client, pdf_bytes(), headers={"X-Forwarded-For": f"203.0.113.{n}"}).status_code)

        held = [threading.Thread(target=attempt, args=(n,)) for n in (1, 2)]
        for t in held:
            t.start()
        for _ in held:
            assert entered.acquire(timeout=30)
        r = post(client, pdf_bytes(), headers={"X-Forwarded-For": "203.0.113.3"})
        assert r.status_code == 503, r.text
        assert r.json() == {"code": "overloaded", "message": upload.MESSAGES["overloaded"]}
        assert r.headers["retry-after"] == upload.RETRY_AFTER_OVERLOADED
        assert rate_rows(settings) == 2
        release.set()
        for t in held:
            t.join(30)
        assert statuses == [201, 201]
        assert post(client, pdf_bytes(), headers={"X-Forwarded-For": "203.0.113.4"}).status_code == 201
    assert job_count(settings) == 3


def test_one_client_holds_at_most_max_uploads_per_client_and_never_all_the_slots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate r2: two uploads held inside the route from one /64; a third from the same /64 is 429 at once with no
    slot spent, while another client still gets one of the server's remaining slots — so one address can no
    longer pin every slot and lock everyone else out."""
    settings = Settings(jobs_dir=tmp_path / "jobs", max_uploads=4, max_uploads_per_client=2, trusted_proxy="testclient")
    entered, release, holding = threading.Semaphore(0), threading.Event(), True
    real = upload.run_preflight

    def slow_preflight(*args, **kwargs):
        # Only the two uploads started while `holding` wait; the ones sent afterwards run through.
        if holding:
            entered.release()
            assert release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(upload, "run_preflight", slow_preflight)
    statuses: list[int] = []
    with TestClient(create_app(settings)) as client:

        def attempt(xff: str) -> None:
            statuses.append(post(client, pdf_bytes(), headers={"X-Forwarded-For": xff}).status_code)

        held = [threading.Thread(target=attempt, args=(a,)) for a in ("2001:db8:1:2::1", "2001:db8:1:2::2")]
        for t in held:
            t.start()
        for _ in held:
            assert entered.acquire(timeout=30)
        holding = False
        r = post(client, pdf_bytes(), headers={"X-Forwarded-For": "2001:db8:1:2::3"})
        assert r.status_code == 429, r.text
        assert r.json() == {"code": "rate_limited", "message": upload.MESSAGES["rate_limited"]}
        assert r.headers["retry-after"] == upload.RETRY_AFTER_OVERLOADED
        assert r.headers["connection"] == "close"
        assert rate_rows(settings) == 2
        # Two of four slots are taken; a client elsewhere gets one of the other two.
        assert post(client, pdf_bytes(), headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 201
        assert rate_rows(settings) == 3
        release.set()
        for t in held:
            t.join(30)
        assert statuses == [201, 201]
        # The cap frees with the uploads: the same /64 is welcome again.
        assert post(client, pdf_bytes(), headers={"X-Forwarded-For": "2001:db8:1:2::3"}).status_code == 201
    assert job_count(settings) == 4


# --- the watchdog (gate r2): a body that stops arriving is abandoned --------------------------------


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Sink:
    """What the guard wraps in these tests, standing in for the multipart parser: reads the whole body, then
    answers 201 — the answer the guard must drop once it has sent a 408 of its own."""

    def __init__(self) -> None:
        self.got, self.disconnected = 0, False

    async def __call__(self, scope, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self.disconnected = True
                break
            self.got += len(message["body"])
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def upload_scope(length: int) -> dict:
    return {
        "type": "http", "http_version": "1.1", "method": "POST", "path": "/api/jobs", "scheme": "http",
        "query_string": b"", "server": ("test", 80), "client": ("203.0.113.9", 1234),
        "headers": [(b"content-type", b"multipart/form-data; boundary=x"), (b"content-length", str(length).encode())],
    }


def trickling(clock: Clock, steps: list[tuple]):
    """A `receive` that plays `steps` — advance the clock by `dt`, then hand over `body` (the last step of a
    complete body says `False` for more) — and, past a `None` body, a client that never sends another byte
    (blocks until the guard's wait gives up)."""
    it, silent = iter(steps), False

    async def receive():
        nonlocal silent
        if not silent:
            dt, body, *more = next(it)
            clock.t += dt
            if body is not None:
                return {"type": "http.request", "body": body, "more_body": more[0] if more else True}
            silent = True
        await asyncio.Event().wait()

    return receive


def guarded(tmp_path: Path, clock, **overrides):
    settings = Settings(jobs_dir=tmp_path / "jobs", **overrides)
    settings.jobs_dir.mkdir()
    store = Store(settings.db_path)
    store.init()
    store.close()
    sink, sent = Sink(), []

    async def send(message):
        sent.append(message)

    return upload.UploadGuard(sink, settings, clock=clock), sink, sent, send


def statuses(sent: list) -> list[int]:
    return [m["status"] for m in sent if m["type"] == "http.response.start"]


def test_an_upload_that_keeps_moving_streams_to_the_app_untouched(tmp_path: Path) -> None:
    """40 KiB every 10 s clears 32 KiB per 30 s window: three windows pass, the app gets every byte, one 201."""
    clock = Clock()
    guard, sink, sent, send = guarded(tmp_path, clock)
    chunk = b"0" * (40 * 1024)
    receive = trickling(clock, [(10.0, chunk)] * 8 + [(10.0, chunk, False)])
    asyncio.run(guard(upload_scope(len(chunk) * 9), receive, send))
    assert sink.got == len(chunk) * 9 and not sink.disconnected
    assert statuses(sent) == [201]
    assert guard.in_flight == 0 and guard.per_client == {}


def test_an_upload_under_the_rate_floor_is_408_too_slow_and_frees_its_slots(tmp_path: Path) -> None:
    """100 bytes in 31 s: the window closes with too little, so that chunk is never handed on — the guard answers
    408 itself (Connection: close), tells the parser the client is gone and drops the parser's own answer. The
    slot the window spent stays spent — a trickler pays with their own hour — but the in-flight counts are freed."""
    clock = Clock()
    guard, sink, sent, send = guarded(tmp_path, clock)
    receive = trickling(clock, [(31.0, b"0" * 100), (1.0, b"0" * 100)])
    asyncio.run(guard(upload_scope(1000), receive, send))
    assert sink.disconnected and sink.got == 0
    assert statuses(sent) == [408]
    headers = {k.decode(): v.decode() for k, v in sent[0]["headers"]}
    assert headers["connection"] == "close"
    assert json.loads(sent[1]["body"]) == {"code": "too_slow", "message": upload.MESSAGES["too_slow"]}
    assert guard.in_flight == 0 and guard.per_client == {}
    assert rate_rows(guard.settings) == 1


def test_a_silent_client_is_408_when_the_wait_itself_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other path to the same answer: nothing arrives at all, so it is the wait that gives up (a 50 ms
    window on the real clock, so the test waits milliseconds, not 30 s). A first window with enough in it
    (100 bytes ≥ a floor of 1) rolls over; the empty one after it refuses."""
    monkeypatch.setattr(upload, "UPLOAD_WINDOW", 0.05)
    guard, sink, sent, send = guarded(tmp_path, time.monotonic, min_upload_rate=1)
    receive = trickling(Clock(), [(0.0, b"0" * 100), (0.0, None)])
    asyncio.run(guard(upload_scope(1000), receive, send))
    assert sink.disconnected and sink.got == 100
    assert statuses(sent) == [408]
    assert guard.in_flight == 0 and guard.per_client == {}


def test_the_app_spools_uploads_under_the_jobs_dir_while_it_runs(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """tempfile (what Starlette's multipart parser spools through) and TMPDIR (what the preflight and preview
    subprocesses inherit) point at `<jobs>/.spool` between startup and shutdown, and at what they were before
    afterwards."""
    monkeypatch.setenv("TMPDIR", "/tmp")
    monkeypatch.setattr(tempfile, "tempdir", None)
    before = tempfile.gettempdir()
    assert not settings.spool_dir.exists()
    with TestClient(create_app(settings)):
        assert settings.spool_dir.is_dir()
        assert tempfile.gettempdir() == str(settings.spool_dir) == os.environ["TMPDIR"]
        with tempfile.NamedTemporaryFile() as f:
            assert Path(f.name).parent == settings.spool_dir
    assert tempfile.gettempdir() == before and os.environ["TMPDIR"] == "/tmp"


JOB_ID = "-bCdEfGhIjKlMnOpQrSt00"  # the token_urlsafe(16) shape, with the awkward leading `-`
ROUTE_WORDS = ("health", "sheets", "sections", "result.zip", "plan", "cut")


def assert_id_gone(out: str, job_id: str) -> None:
    """Neither the id nor any 16-char window of it (enough to brute-force the rest) survives."""
    assert job_id not in out
    assert not any(job_id[i : i + 16] in out for i in range(len(job_id) - 15))


def test_redact_path() -> None:
    assert redact_path(f"/api/jobs/{JOB_ID}") == f"/api/jobs/{log_id(JOB_ID)}"
    assert redact_path(f"/api/jobs/{JOB_ID}/sheets/3.png") == f"/api/jobs/{log_id(JOB_ID)}/sheets/3.png"
    assert redact_path("/api/jobs") == "/api/jobs"
    assert redact_path("/api/health") == "/api/health"
    # Route words stay readable: none is a 16+ run of the id alphabet and none sits in the id slot.
    for word in ROUTE_WORDS:
        assert redact_path(f"/api/jobs/{JOB_ID}/{word}") == f"/api/jobs/{log_id(JOB_ID)}/{word}"
        assert redact_path(f"/api/{word}") == f"/api/{word}"
    # Rule 1: a 16+ run of the id alphabet is hashed whole wherever it sits; 15 is left alone.
    assert redact_path("/x/" + "a" * 15) == "/x/" + "a" * 15
    assert redact_path("/x/" + "a" * 16) == "/x/" + log_id("a" * 16)
    assert redact_path("/x/" + "a" * 11 + "." + "a" * 10) == "/x/" + "a" * 11 + "." + "a" * 10
    # Rule 2: whatever sits in the slot after `/api/jobs/` is hashed, however short.
    assert redact_path("/api/jobs/ab") == "/api/jobs/" + log_id("ab")
    assert redact_path("/API//jobs//ab/x") == "/API//jobs//" + log_id("ab") + "/x"


@pytest.mark.parametrize(
    "glue",
    [
        lambda i: i + "x",
        lambda i: "x" + i,
        lambda i: i + i,
        lambda i: i + "-extra",
        lambda i: i + "_",
        lambda i: i + "A",  # what `%41` decodes to
        lambda i: i[:21],
        lambda i: i[:16],
    ],
    ids=["id+x", "x+id", "id+id", "id-extra", "id_", "id+A", "id[:21]", "id[:16]"],
)
@pytest.mark.parametrize("shape", ["/api/jobs/{v}", "/x/{v}", "/{v}/sheets/1.png", "{v}"])
def test_redact_path_hashes_an_id_glued_to_other_alphabet_chars(shape: str, glue) -> None:
    for job_id in (JOB_ID, upload.new_job_id()):
        path = shape.format(v=glue(job_id))
        out = redact_path(path)
        assert out != path
        assert_id_gone(out, job_id)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("//api/jobs/{id}", "//api/jobs/{h}"),
        ("/api/jobs//{id}", "/api/jobs//{h}"),
        ("/api//jobs/{id}", "/api//jobs/{h}"),
        ("/API/jobs/{id}", "/API/jobs/{h}"),
        ("/api/jobs/./{id}", "/api/jobs/{dot}/{h}"),
        ("/api/jobs/../jobs/{id}", "/api/jobs/{dotdot}/jobs/{h}"),
        ("/http://h:1/api/jobs/{id}", "/http://h:1/api/jobs/{h}"),
        ("/api/jobs/{id}.json", "/api/jobs/{h_json}"),
        ("/api/jobs/{id};v=1/sheets/1.png", "/api/jobs/{h_v1}/sheets/1.png"),
        ("/{id}", "/{h}"),
        ("{id}", "{h}"),
        ("/api/jobs/{id}/sheets/{id}", "/api/jobs/{h}/sheets/{h}"),
    ],
)
def test_redact_path_cannot_be_dodged_by_path_shape(path: str, expected: str) -> None:
    for job_id in (JOB_ID, upload.new_job_id()):
        out = redact_path(path.format(id=job_id))
        assert_id_gone(out, job_id)
        # The slot after `/api/jobs/` is hashed as one segment, so `<id>.json` there is log_id("<id>.json").
        assert out == expected.format(
            h=log_id(job_id),
            dot=log_id("."),
            dotdot=log_id(".."),
            h_json=log_id(job_id + ".json"),
            h_v1=log_id(job_id + ";v=1"),
        )


def test_logged_path_is_escaped(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    """The ASGI path arrives percent-decoded: `%1B%5B31m` is a real ESC by the time it is logged."""
    caplog.set_level(logging.INFO)
    with TestClient(create_app(settings)) as client:
        client.get(f"/api/jobs/{JOB_ID}/%1B%5B31m")
        client.get(f"/api/%E2%80%A8%C2%85/jobs/{JOB_ID}%0A")
        client.get("/api/health/%25/x")
        client.get(f"/api/jobs/%1B%5B31m/{JOB_ID}")
        client.get(f"/x/{JOB_ID}%41")
    access = [r.getMessage() for r in caplog.records if r.name == "pdf_splitter.access"]
    assert len(access) == 5
    for line in access:
        assert line.isascii() and line.isprintable()
        assert_id_gone(line, JOB_ID)
    assert access[0].startswith(f"GET /api/jobs/{log_id(JOB_ID)}/%1B%5B31m 404 ")
    assert access[1].startswith(f"GET /api/%E2%80%A8%C2%85/jobs/{log_id(JOB_ID)}%0A 404 ")
    # A literal `%` is re-encoded, so the logged path is unambiguous.
    assert access[2].startswith("GET /api/health/%25/x 404 ")
    # Garbage in the id slot is hashed rather than echoed (escaped) back.
    assert access[3].startswith(f"GET /api/jobs/{log_id(chr(27) + '[31m')}/{log_id(JOB_ID)} 404 ")
    # `%41` glues an `A` onto the id after decoding; the whole run is hashed.
    assert access[4].startswith(f"GET /x/{log_id(JOB_ID + 'A')} 404 ")


def test_logs_never_contain_a_job_id(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(settings)) as client:
        job_id = post(client, pdf_bytes()).json()["id"]
        client.get(f"/api/jobs/{job_id}")
        client.get(f"/api/jobs/{job_id}/sheets/1.png?dpi=72")
        post(client, image_only_pdf())  # a rejection logs too
    # httpx2 is the test CLIENT logging its own request URLs; everything else is the server.
    server = [r.getMessage() for r in caplog.records if not r.name.startswith("httpx")]
    assert server and all(job_id not in line for line in server)
    access = [r.getMessage() for r in caplog.records if r.name == "pdf_splitter.access"]
    assert any(line.startswith("POST /api/jobs 201 ") for line in access)
    assert any(line.startswith(f"GET /api/jobs/{log_id(job_id)} 200 ") for line in access)
    assert any(f"/api/jobs/{log_id(job_id)}/sheets/1.png 409" in line for line in access)
    assert all("dpi" not in line for line in access)
