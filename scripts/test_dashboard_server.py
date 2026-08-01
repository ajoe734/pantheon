#!/usr/bin/env python3
from __future__ import annotations

import functools
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".orchestrator"))

import common  # noqa: E402
import dashboard_server  # noqa: E402


def test_refresh_environment_uses_operator_identity_and_drops_worker_lease(monkeypatch) -> None:
    worker_env = {
        "AI_NAME": "Codex2",
        "ORCH_RUN_ID": "codex-run",
        "PANTHEON_WORKTREE_ROOT": "/tmp/task-worktree",
        "ORCH_WORKSPACE_PATH": "/tmp/task-worktree",
        "PANTHEON_COMMAND_ROOT": "/tmp/command-root",
    }
    for name, value in worker_env.items():
        monkeypatch.setenv(name, value)

    repo_root = Path("/srv/pantheon")
    env = dashboard_server.dashboard_refresh_environment(repo_root)

    assert env["AI_NAME"] == "Human/ops"
    assert env["PANTHEON_STATUS_ROOT"] == str(repo_root)
    for name in dashboard_server.WORKER_CONTEXT_ENV_NAMES:
        assert name not in env
    assert os.environ["ORCH_RUN_ID"] == "codex-run"


def test_refresh_environment_allows_explicit_operator_actor(monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_DASHBOARD_REFRESH_ACTOR", "Ops")

    env = dashboard_server.dashboard_refresh_environment(Path("/srv/pantheon"))

    assert env["AI_NAME"] == "Ops"


def test_activity_get_waits_for_audit_writer_and_returns_complete_tail() -> None:
    with tempfile.TemporaryDirectory(prefix="dashboard-audit-lock-") as tmpdir:
        root = Path(tmpdir)
        log_path = root / "ai-activity-log.jsonl"
        expected = b'{"event_id":"one"}\n{"event_id":"two"}\n'
        log_path.write_bytes(expected)
        dashboard_server.NoCacheRequestHandler.live_file_map = {
            "/ai-activity-log.jsonl": log_path,
        }
        dashboard_server.NoCacheRequestHandler.tail_line_map = {
            "/ai-activity-log.jsonl": 500,
        }
        handler = functools.partial(
            dashboard_server.NoCacheRequestHandler,
            directory=str(root),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        result: list[bytes] = []
        error: list[BaseException] = []

        def fetch() -> None:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{server.server_port}/ai-activity-log.jsonl",
                    timeout=5,
                ) as response:
                    result.append(response.read())
            except BaseException as exc:  # pragma: no cover - assertion reports it
                error.append(exc)

        try:
            with common.activity_audit_lock_file(
                log_path,
                shared=False,
                nonblocking=False,
            ):
                request_thread = threading.Thread(target=fetch)
                request_thread.start()
                time.sleep(0.2)
                assert request_thread.is_alive(), "dashboard crossed audit writer lock"
            request_thread.join(timeout=5)
            assert not request_thread.is_alive()
            assert error == []
            assert result == [expected]
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
