from __future__ import annotations

import argparse
import logging
import signal
import threading

import uvicorn

from .app import create_app
from .config import Settings
from .worker.runner import Runner


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pdf-splitter")
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api", help="serve the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="run queued jobs in sandboxed subprocesses")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "api":
        # uvicorn's access log would print raw job ids from /api/jobs/<id> paths; app.access_log
        # logs the same line with the id hashed. proxy_headers=False: uvicorn would otherwise rewrite
        # request.client from X-Forwarded-For for any 127.0.0.1 peer, ahead of ratelimit.client_ip, whose
        # PDFSPLIT_TRUSTED_PROXY rule must be the only place a forwarded address is believed.
        uvicorn.run(
            create_app(Settings()), host=args.host, port=args.port, access_log=False, proxy_headers=False
        )
    elif args.command == "worker":
        stop = threading.Event()
        # `docker stop` sends SIGTERM: stop claiming and let running jobs finish; a job cut short by the
        # container's final kill is re-queued by the next start's sweep.
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        Runner(Settings(), kinds=("analyze", "cut")).serve(stop)
