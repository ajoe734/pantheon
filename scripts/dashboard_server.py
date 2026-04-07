#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import json
import subprocess
import shutil
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class NoCacheRequestHandler(SimpleHTTPRequestHandler):
    live_file_map: dict[str, Path] = {}
    repo_root: Path | None = None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/__refresh":
            self.handle_refresh()
            return
        live_path = self.live_file_map.get(parsed.path)
        if live_path is not None:
            if not live_path.exists():
                self.send_error(404, f"Live file not found: {parsed.path}")
                return
            self.send_response(200)
            self.send_header("Content-type", self.guess_type(str(live_path)))
            self.send_header("Content-Length", str(live_path.stat().st_size))
            self.end_headers()
            with live_path.open("rb") as source:
                shutil.copyfileobj(source, self.wfile)
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/__refresh":
            self.handle_refresh()
            return
        self.send_error(404, "Not Found")

    def handle_refresh(self) -> None:
        repo_root = self.repo_root
        if repo_root is None:
            self.send_error(500, "Repo root not configured")
            return
        try:
            result = subprocess.run(
                ["python3", str(repo_root / "scripts" / "ai_status.py"), "sync"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=True,
            )
            payload = {
                "ok": True,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except subprocess.CalledProcessError as exc:
            payload = {
                "ok": False,
                "stdout": exc.stdout,
                "stderr": exc.stderr,
                "returncode": exc.returncode,
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve local orchestrator dashboard assets without browser caching.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=4173, help="Port to bind. Default: 4173")
    parser.add_argument(
        "--directory",
        default=str(Path(__file__).resolve().parents[1] / "docs-site"),
        help="Directory to serve. Default: repo/docs-site",
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root for live state files. Default: current repo root",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    directory = str(Path(args.directory).resolve())
    repo_root = Path(args.repo_root).resolve()
    NoCacheRequestHandler.live_file_map = {
        "/ai-status.json": repo_root / "ai-status.json",
        "/ai-activity-log.jsonl": repo_root / "ai-activity-log.jsonl",
        "/current-work.md": repo_root / "current-work.md",
        "/orchestrator-state.json": repo_root / ".orchestrator" / "state.json",
        "/approval-queue.json": repo_root / ".orchestrator" / "approval-queue.json",
    }
    NoCacheRequestHandler.repo_root = repo_root
    handler = functools.partial(NoCacheRequestHandler, directory=directory)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving dashboard at http://{args.host}:{args.port}/index.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
