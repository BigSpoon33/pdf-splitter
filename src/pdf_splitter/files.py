"""Files the api and the worker both read and write, always replaced whole (write-then-rename)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any) -> None:
    # Write-then-rename: the api may read these files while the task runs, and must never see half of one.
    tmp = path.with_name(path.name + ".tmp")
    try:
        # `analyze.json_safe` has already replaced lone surrogates; `errors="replace"` is the backstop, so a
        # string that slipped past it costs a `?`, never the whole file.
        tmp.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8", errors="replace"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8"))
