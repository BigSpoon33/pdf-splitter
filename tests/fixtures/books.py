"""Synthetic books for the worker tests, ported from the engine's `tests/fixtures.py` (`FakeBook`,
`headed_book`) so this repo never imports the engine's tests. Coordinates are BASELINES (insert_text)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

W, H = 522.72, 789.6
LEFT, RIGHT = 53, 271
LINE = 12


class FakeBook:
    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.cur = None

    def text(self, y: float, s: str, size: float = 9, x: float = LEFT, font: str = "helv") -> float:
        self.cur.insert_text((x, y), s, fontsize=size, fontname=font)
        return y + LINE

    def save(self, path: Path) -> str:
        self.doc.save(str(path))
        return str(path)


def headed_book(path: Path, outline: bool = True) -> dict:
    """A generic two-column book (web mode: page = 1-based sheet): body 9.5pt, chapters 16pt bold,
    sections 12pt bold, a 14pt running header and a 12.5pt folio inside the default bands.
      p1  Chapter 1 top-left; section 'First Principles' mid-left
      p2  a section wrapped over two lines in the right column
      p3  Chapter 2 full-width at the top; section 'Middle Matters' left
      p4  Chapter 3 starts mid-right-column
      p5  section 'Final Section' top-right
      p6  body only
    Outline (`outline=True`): chapters at level 1, sections at level 2 with leading numbers; '3.1' is a URI
    (page -1), so the engine drops it: 3 items at level 1, 3 at level 2."""
    b = FakeBook()
    running = "The Synthetic Handbook"

    def page(n: int) -> None:
        b.cur = b.doc.new_page(width=W, height=H)
        b.text(35, running, size=14, x=54, font="hebo")
        b.text(775, str(n), size=12.5, x=255, font="hebo")

    def body(y: float, n: int, col: str = "left", prefix: str = "body") -> float:
        x = LEFT if col == "left" else RIGHT
        for i in range(n):
            b.text(y + LINE * i, f"{prefix} {i} of the running prose", size=9.5, x=x)
        return y + LINE * n

    def head(y: float, s: str, size: float, col: str = "left") -> float:
        return b.text(y, s, size=size, x=LEFT if col == "left" else RIGHT, font="hebo") + 8

    page(1); y = head(90, "Foundations of Testing", 16); y = body(y, 20)
    y = head(y + 10, "First Principles", 12)
    body(y, 25); body(80, 55, "right")
    page(2); body(80, 55); y = body(80, 20, "right")
    y = head(y + 12, "Second Principles of Wrapped", 12, "right")
    y = head(y - 8 + 14 - LINE, "Section Headings", 12, "right"); body(y, 25, "right")
    page(3); y = head(90, "Chapter Two: The Middle of the Synthetic Book", 16); y = body(y + 4, 20)
    y = head(y + 10, "Middle Matters", 12); body(y, 22); body(122, 50, "right")
    page(4); body(80, 55); y = body(80, 25, "right"); y = head(y + 20, "Closing Chapter", 16, "right")
    body(y, 25, "right")
    page(5); body(80, 55); y = head(90, "Final Section", 12, "right"); body(y, 50, "right")
    page(6); body(80, 55); body(80, 55, "right")
    if outline:
        b.doc.set_toc([
            [1, "1 Foundations of Testing", 1,
             {"kind": pymupdf.LINK_GOTO, "to": pymupdf.Point(0, 70), "page": 0}],
            [2, "1.1 First Principles", 1],
            [2, "1.2 Second Principles of Wrapped Section Headings", 2],
            [1, "2 Chapter Two: The Middle of the Synthetic Book", 3],
            [2, "2.1 Middle Matters", 3],
            [1, "3 Closing Chapter", 4],
            [2, "3.1 Final Section", 5],
        ])
        items = {it[1]: it[3]["xref"] for it in b.doc.get_toc(simple=False)}
        b.doc.xref_set_key(items["3.1 Final Section"], "A", "<</S/URI/URI(https://example.com/)>>")
    b.save(path)
    return {
        "pdf": str(path),
        "chapters": [
            {"name": "Foundations of Testing", "page": 1},
            {"name": "Chapter Two: The Middle of the Synthetic Book", "page": 3},
            {"name": "Closing Chapter", "page": 4},
        ],
        "sections": [
            {"name": "First Principles", "page": 1},
            {"name": "Second Principles of Wrapped Section Headings", "page": 2},
            {"name": "Middle Matters", "page": 3},
            {"name": "Final Section", "page": 5},
        ],
    }


def text_book(path: Path, pages: int) -> str:
    """`pages` sheets of plain body text: enough text layer for the engine, nothing to detect."""
    b = FakeBook()
    for n in range(pages):
        b.cur = b.doc.new_page(width=W, height=H)
        for i in range(20):
            b.text(80 + LINE * i, f"page {n} line {i} of plain body prose")
    return b.save(path)
