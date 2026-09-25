from __future__ import annotations

import argparse
import logging

import uvicorn

from .app import create_app
from .config import Settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pdf-splitter")
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api", help="serve the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "api":
        uvicorn.run(create_app(Settings()), host=args.host, port=args.port)
