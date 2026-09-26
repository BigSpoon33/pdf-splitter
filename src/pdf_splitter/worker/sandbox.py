"""Resource limits for every subprocess that parses an uploaded PDF (ADR-005).

`python -m pdf_splitter.worker.sandbox --as N --cpu N --fsize N -- <python args...>` sets the limits on
itself and then execs `sys.executable <python args...>`; rlimits survive exec, so the job runs as the exact
command it names (`python -m pdf_splitter.task analyze -- <id>`) with the limits in force before it reads
a byte.
A launcher instead of `preexec_fn` because the worker spawns from several threads at once, and `preexec_fn` is
not safe in a threaded parent (the child can deadlock on a lock another thread held at fork time).
"""

from __future__ import annotations

import argparse
import os
import resource
import sys

GB = 1024**3
AS_BYTES = 2 * GB
FSIZE_BYTES = 1 * GB
CPU_GRACE_S = 10
MODULE = "pdf_splitter.worker.sandbox"


def limits(timeout: float) -> dict[str, int]:
    """CPU gets a grace over the wall timeout, so the wall clock is what normally ends a slow job and
    RLIMIT_CPU only catches one that burns CPU on several threads faster than wall time passes."""
    return {"as": AS_BYTES, "cpu": int(timeout) + CPU_GRACE_S, "fsize": FSIZE_BYTES}


def apply(lim: dict[str, int]) -> None:
    # soft == hard: the child can never raise its own limits back.
    resource.setrlimit(resource.RLIMIT_AS, (lim["as"], lim["as"]))
    resource.setrlimit(resource.RLIMIT_CPU, (lim["cpu"], lim["cpu"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (lim["fsize"], lim["fsize"]))


def command(lim: dict[str, int], python_args: list[str]) -> list[str]:
    return [
        sys.executable, "-m", MODULE,
        "--as", str(lim["as"]), "--cpu", str(lim["cpu"]), "--fsize", str(lim["fsize"]),
        "--", *python_args,
    ]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--" not in argv:
        print(f"usage: python -m {MODULE} --as N --cpu N --fsize N -- ARGS...", file=sys.stderr)
        return 2
    split = argv.index("--")
    parser = argparse.ArgumentParser(prog=f"python -m {MODULE}")
    parser.add_argument("--as", dest="as_", type=int, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--fsize", type=int, required=True)
    opts = parser.parse_args(argv[:split])
    python_args = argv[split + 1 :]
    if not python_args:
        parser.error("nothing to run after --")
    apply({"as": opts.as_, "cpu": opts.cpu, "fsize": opts.fsize})
    # Only ever this interpreter: the launcher runs Python code, never an arbitrary binary.
    os.execv(sys.executable, [sys.executable, *python_args])
    return 0  # pragma: no cover - execv does not return


if __name__ == "__main__":
    sys.exit(main())
