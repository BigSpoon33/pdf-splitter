"""Hand-built hostile PDFs (from the STORY-006 review's repros): PyMuPDF would normalise what these tests
need, so the objects are written byte for byte. Every file has an ordinary text page first, so the preflight's
text probe passes and the worker is what meets the problem."""

from __future__ import annotations

from pathlib import Path


def build(path: Path, objs: list[bytes]) -> Path:
    """`objs[i]` is the body of object i+1; object 1 must be the catalog."""
    buf = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(buf)
    buf += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offsets:
        buf += f"{o:010d} 00000 n \n".encode()
    buf += f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(buf))
    return path


def stream(dict_extra: str, data: bytes) -> bytes:
    return f"<<{dict_extra}/Length {len(data)}>>\nstream\n".encode() + data + b"\nendstream"


def text_content(lines: int = 30) -> bytes:
    body = " ".join(f"(Body text line of the ordinary kind {i}) '" for i in range(lines))
    return f"BT /F1 10 Tf 50 750 Td 14 TL {body} ET".encode()


PAGE = b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 3 0 R>>>>/Contents %d 0 R>>"


def xobj_bomb(path: Path, depth: int = 6, fan: int = 10, chars: int = 60) -> Path:
    """4.9 KB, 2 pages: page 2 draws a Form XObject that draws `fan` copies of the next one, `depth` deep, so
    text extraction expands fan**depth (10^6) glyph runs. Under RLIMIT_AS 2 GB MuPDF's allocator fails inside
    the extraction, which surfaces as a RuntimeError, not a MemoryError."""
    objs: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[4 0 R 6 0 R]/Count 2>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        PAGE % 5,
        stream("", text_content()),
        b"",  # page 2, filled in once the outermost form's object number is known
        stream("", text_content() + b" q /B Do Q"),
    ]
    objs.append(stream("/Type/XObject/Subtype/Form/BBox[0 0 612 792]/Resources<</Font<</F1 3 0 R>>>>",
                       f"BT /F1 9 Tf 10 10 Td ({'A' * chars}) Tj ET".encode()))
    prev = len(objs)
    for _ in range(depth):
        objs.append(stream(f"/Type/XObject/Subtype/Form/BBox[0 0 612 792]/Resources<</XObject<</X {prev} 0 R>>>>",
                           " ".join(["/X Do"] * fan).encode()))
        prev = len(objs)
    objs[5] = (b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 3 0 R>>"
               + f"/XObject<</B {prev} 0 R>>>>/Contents 7 0 R>>".encode())
    return build(path, objs)


def link_uri_bomb(path: Path, links: int = 40_000, uri_len: int = 65_536) -> Path:
    """309 KB, 2 pages: page 2's /Annots lists the same Link annotation `links` times, and its URI is one
    `uri_len`-byte string that MuPDF copies once per link while LOADING the page (`fz_load_page`, a raw
    binding, before any text extraction). Under RLIMIT_AS 2 GB the allocator fails there, and PyMuPDF raises
    `pymupdf.mupdf.FzErrorSystem`, not a RuntimeError (the STORY-006 gate r2 repro)."""
    uri = "http://e/" + "A" * (uri_len - 9)
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[4 0 R 6 0 R]/Count 2>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        PAGE % 5,
        stream("", text_content()),
        (b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 3 0 R>>>>"
         b"/Contents 7 0 R/Annots 8 0 R>>"),
        stream("", text_content()),
        b"[" + " ".join(["9 0 R"] * links).encode() + b"]",
        b"<</Type/Annot/Subtype/Link/Rect[0 0 10 10]/Border[0 0 0]/A<</S/URI/URI 10 0 R>>>>",
        b"(" + uri.encode() + b")",
    ]
    return build(path, objs)


def outline_book(path: Path, title_hex: list[str]) -> Path:
    """2 text pages and a flat outline whose titles are the given hex strings, byte for byte (a PDF text string:
    a UTF-16BE BOM `FEFF…`, a UTF-8 BOM `EFBBBF…`, or PDFDocEncoding). Items alternate between the two pages."""
    n = len(title_hex)
    objs: list[bytes] = [
        b"<</Type/Catalog/Pages 2 0 R/Outlines 8 0 R/PageMode/UseOutlines>>",
        b"<</Type/Pages/Kids[4 0 R 6 0 R]/Count 2>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        PAGE % 5,
        stream("", text_content()),
        PAGE % 7,
        stream("", text_content()),
        f"<</Type/Outlines/First 9 0 R/Last {8 + n} 0 R/Count {n}>>".encode(),
    ]
    for i, hx in enumerate(title_hex):
        num = 9 + i
        prev = f"/Prev {num - 1} 0 R" if i > 0 else ""
        nxt = f"/Next {num + 1} 0 R" if i < n - 1 else ""
        page = 4 if i % 2 == 0 else 6
        objs.append(f"<</Title <{hx}>/Parent 8 0 R{prev}{nxt}/Dest[{page} 0 R/XYZ 0 700 0]>>".encode())
    return build(path, objs)
