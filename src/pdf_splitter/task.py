"""`python -m pdf_splitter.task <kind> -- <id>`: the command AC-1 names; the code is `worker/task.py`."""

from __future__ import annotations

import sys

from .worker.task import main

if __name__ == "__main__":
    sys.exit(main())
