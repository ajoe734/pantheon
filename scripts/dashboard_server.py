#!/usr/bin/env python3
from __future__ import annotations

import argparse
import functools
import json
import os
import subprocess
import shutil
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from common import load_config, read_activity_log_tail_bytes


DASHBOARD_REFRESH_ACTOR_ENV = "PANTHEON_DASHBOARD_REFRESH_ACTOR"
DASHBOARD_REFRESH_ACTOR_DEFAULT = "Human/ops"
WORKER_CONTEXT_ENV_NAMES = (
    "ORCH_RUN_ID",
    "ORCH_TASK_ID",
    "ORCH_RUNNER_STATUS_PATH",
    "ORCH_HEARTBEAT_PATH",
    "PANTHEON_WORKTREE_ROOT",
    "ORCH_WORKSPACE_PATH",
    "PANTHEON_COMMAND_ROOT",
    "PANTHEON_COMMAND_RUNTIME_SHA",
    "PANTHEON_COMMAND_REMOTE",
    "PANTHEON_COMMAND_BASE_REF",
    "PANTHEON_STATUS_COMMAND_WRAPPER_ROOT",
)


def dashboard_refresh_environment(repo_root: Path) -> dict[str, str]:
    """Build an operator-scoped environment for dashboard projection refreshes."""

    env = os.environ.copy()
    actor = str(env.get(DASHBOARD_REFRESH_ACTOR_ENV) or "").strip()
    env["AI_NAME"] = actor or DASHBOARD_REFRESH_ACTOR_DEFAULT
    env["PANTHEON_STATUS_ROOT"] = str(repo_root)
    for name in WORKER_CONTEXT_ENV_NAMES:
        env.pop(name, None)
    # ai_status.py's canonical mutation lease check rejects every command
    # (including the no-op "sync" this refresh runs) unless it sees either an
    # active worker lease (ORCH_RUN_ID, stripped above) or this explicit local
    # Human/Ops opt-in. Without it every dashboard refresh 500s.
    env["PANTHEON_LOCAL_HUMAN_OPS"] = "1"
    return env


def repo_root_is_authoritatively_governed(repo_root: Path) -> bool:
    """True when a live supervisor/worker fleet already keeps this root current.

    A journal-governed root's projection is rewritten by every governed
    mutation (task done/approve/reassign/etc), so it is already as fresh as
    this process can observe. Running the refresh subprocess against it
    anyway would use ai_status.py's *non*-authoritative fallback path (this
    dashboard has no journal identity of its own to run authoritatively
    with) -- diverging from the journal that real workers treat as ground
    truth, exactly the drain-without-audit condition the journal's own
    task_state_drain marker check exists to reject. Skip the subprocess
    entirely rather than risk that divergence; there is nothing for it to
    usefully refresh here.
    """

    try:
        config = load_config(str(repo_root / ".orchestrator" / "config.json"))
    except (OSError, ValueError):
        return False
    store = config.get("task_state_store")
    if not isinstance(store, dict):
        return False
    return str(store.get("mode") or "").strip().lower() == "authoritative"


class NoCacheRequestHandler(SimpleHTTPRequestHandler):
    live_file_map: dict[str, Path] = {}
    tail_line_map: dict[str, int] = {}  # paths that should be served as last-N lines
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
            tail_lines = self.tail_line_map.get(parsed.path)
            if tail_lines is not None:
                # Recovery runs under audit EX; the returned snapshot is read
                # under audit SH and remains immutable while sent to clients.
                body = read_activity_log_tail_bytes(
                    live_path,
                    max_lines=tail_lines,
                )
                if body is None:
                    self.send_error(404, f"Live file not found: {parsed.path}")
                    return
                self.send_response(200)
                self.send_header("Content-type", self.guess_type(str(live_path)))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
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
        if repo_root_is_authoritatively_governed(repo_root):
            payload = {
                "ok": True,
                "stdout": (
                    "Skipped: this projection is a journal-governed live root "
                    "kept current by the fleet itself; no refresh needed."
                ),
                "stderr": "",
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            result = subprocess.run(
                ["bash", str(repo_root / "scripts" / "sync-state.sh")],
                cwd=str(repo_root),
                env=dashboard_refresh_environment(repo_root),
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
        "/dashboard-bundle.json": repo_root / "dashboard-bundle.json",
        "/orchestrator-state.json": repo_root / ".orchestrator" / "state.json",
        "/approval-queue.json": repo_root / ".orchestrator" / "approval-queue.json",
    }
    # Serve only the last 500 lines of the activity log to keep payload small
    NoCacheRequestHandler.tail_line_map = {
        "/ai-activity-log.jsonl": 500,
    }
    NoCacheRequestHandler.repo_root = repo_root
    handler = functools.partial(NoCacheRequestHandler, directory=directory)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving dashboard at http://{args.host}:{args.port}/index.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
