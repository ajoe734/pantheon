#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoCacheRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve Pantheon dashboard assets without browser caching.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=4173, help="Port to bind. Default: 4173")
    parser.add_argument(
        "--directory",
        default=str(Path(__file__).resolve().parents[1] / "docs-site"),
        help="Directory to serve. Default: pantheon/docs-site",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    directory = str(Path(args.directory).resolve())
    handler = functools.partial(NoCacheRequestHandler, directory=directory)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving dashboard at http://{args.host}:{args.port}/index.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
