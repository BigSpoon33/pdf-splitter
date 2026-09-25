from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
import threading
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


def post(client: TestClient, data: bytes, name: str = "book.pdf"):
    return client.post("/api/jobs", files={"file": (name, io.BytesIO(data), "application/pdf")})


def job_count(settings: Settings) -> int:
    store = Store(settings.db_path)
    try:
        return store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        store.close()


def job_dirs(settings: Settings) -> list[Path]:
    return [p for p in settings.jobs_dir.iterdir() if p.is_dir()]


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

    with TestClient(create_app(settings), raise_server_exceptions=True) as client:

        def hammer() -> None:
            try:
                barrier.wait()
                for _ in range(per_thread):
                    r = post(client, data)
                    assert r.status_code == 201, r.text
                    with lock:
                        ids.append(r.json()["id"])
            except BaseException as e:  # noqa: BLE001 - surfaced on the main thread
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert client.get("/api/health").json()["queue"] == n_threads * per_thread

    assert not errors
    assert len(set(ids)) == n_threads * per_thread
    assert job_count(settings) == n_threads * per_thread
    assert {p.name for p in job_dirs(settings)} == set(ids)


JOB_ID = "-bCdEfGhIjKlMnOpQrSt00"  # the token_urlsafe(16) shape, with the awkward leading `-`


def test_redact_path() -> None:
    assert redact_path(f"/api/jobs/{JOB_ID}") == f"/api/jobs/{log_id(JOB_ID)}"
    assert redact_path(f"/api/jobs/{JOB_ID}/sheets/3.png") == f"/api/jobs/{log_id(JOB_ID)}/sheets/3.png"
    assert redact_path("/api/jobs") == "/api/jobs"
    assert redact_path("/api/health") == "/api/health"
    # Not id-shaped: 21 or 23 chars, or a char outside the url-safe alphabet.
    for other in ("/api/jobs/" + "a" * 21, "/api/jobs/" + "a" * 23, "/api/jobs/" + "a" * 11 + "." + "a" * 10):
        assert redact_path(other) == other


@pytest.mark.parametrize(
    "path",
    [
        "//api/jobs/{id}",
        "/api/jobs//{id}",
        "/api//jobs/{id}",
        "/API/jobs/{id}",
        "/api/jobs/./{id}",
        "/api/jobs/../jobs/{id}",
        "/http://h:1/api/jobs/{id}",
        "/api/jobs/{id}.json",
        "/api/jobs/{id};v=1/sheets/1.png",
        "/{id}",
        "{id}",
        "/api/jobs/{id}/sheets/{id}",
    ],
)
def test_redact_path_cannot_be_dodged_by_path_shape(path: str) -> None:
    for job_id in (JOB_ID, upload.new_job_id()):
        out = redact_path(path.format(id=job_id))
        assert job_id not in out
        assert log_id(job_id) in out


def test_logged_path_is_escaped(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    """The ASGI path arrives percent-decoded: `%1B%5B31m` is a real ESC by the time it is logged."""
    caplog.set_level(logging.INFO)
    with TestClient(create_app(settings)) as client:
        client.get(f"/api/jobs/%1B%5B31m/{JOB_ID}")
        client.get(f"/api/jobs/%E2%80%A8%C2%85{JOB_ID}%0A")
        client.get("/api/jobs/%25/x")
    access = [r.getMessage() for r in caplog.records if r.name == "pdf_splitter.access"]
    assert len(access) == 3
    for line in access:
        assert line.isascii() and line.isprintable()
        assert JOB_ID not in line
    assert access[0].startswith(f"GET /api/jobs/%1B%5B31m/{log_id(JOB_ID)} 404 ")
    assert access[1].startswith(f"GET /api/jobs/%E2%80%A8%C2%85{log_id(JOB_ID)}%0A 404 ")
    # A literal `%` is re-encoded, so the logged path is unambiguous.
    assert access[2].startswith("GET /api/jobs/%25/x 404 ")


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
    assert any(line.startswith(f"GET /api/jobs/{log_id(job_id)} 404 ") for line in access)
    assert any(f"/api/jobs/{log_id(job_id)}/sheets/1.png 404" in line for line in access)
    assert all("dpi" not in line for line in access)
