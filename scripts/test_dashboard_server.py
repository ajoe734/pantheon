#!/usr/bin/env python3
from __future__ import annotations

import functools
import json
import os
import subprocess
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


def test_module_imports_standalone_with_no_preseeded_sys_path() -> None:
    """OPS-DASHBOARD-IMPORT-ORDER-FIX-20260821: a top-of-file
    `from common import load_config` placed before the sys.path.insert for
    .orchestrator/ raised ModuleNotFoundError every time the real launcher
    (`python3 scripts/dashboard_server.py`) started it as a fresh process --
    this test file's own path pre-seeding above masked exactly that bug,
    so this spawns a clean subprocess the way production actually does."""

    result = subprocess.run(
        [sys.executable, "-c", "import dashboard_server"],
        cwd=str(ROOT / "scripts"),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


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


def test_refresh_environment_opts_into_local_human_ops(monkeypatch) -> None:
    """OPS-DASHBOARD-REFRESH-LOCAL-HUMAN-OPS-20260821: without this opt-in,
    ai_status.py's canonical mutation lease check rejects every refresh
    (including the no-op "sync" command sync-state.sh runs) with
    "canonical mutation requires an exact active worker lease or explicit
    local Human/Ops mode", so every dashboard refresh click 500s."""

    env = dashboard_server.dashboard_refresh_environment(Path("/srv/pantheon"))

    assert env["PANTHEON_LOCAL_HUMAN_OPS"] == "1"


def test_repo_root_is_authoritatively_governed_true_for_authoritative_store() -> None:
    with tempfile.TemporaryDirectory(prefix="dashboard-governed-") as tmpdir:
        root = Path(tmpdir)
        (root / ".orchestrator").mkdir()
        (root / ".orchestrator" / "config.json").write_text(
            '{"task_state_store": {"mode": "authoritative", "event_log": "x"}}',
            encoding="utf-8",
        )
        assert dashboard_server.repo_root_is_authoritatively_governed(root)


def test_repo_root_is_authoritatively_governed_false_without_config() -> None:
    with tempfile.TemporaryDirectory(prefix="dashboard-ungoverned-") as tmpdir:
        root = Path(tmpdir)
        assert not dashboard_server.repo_root_is_authoritatively_governed(root)


def test_refresh_skips_subprocess_for_a_governed_root() -> None:
    """OPS-DASHBOARD-REFRESH-LOCAL-HUMAN-OPS-20260821: sync-state.sh's
    non-authoritative `ai_status.py sync` would bypass the journal a
    governed root's real workers treat as ground truth -- the exact
    drain-without-audit condition the journal's task_state_drain marker
    check exists to reject (diagnosed live reconciling
    OPS-L12-READBACK-READ-CAP-20260817 / OPS-COMPENSATE-RELEASE-GCP-PROJECT-ID-20260818).
    A governed root must skip the subprocess rather than risk that
    divergence -- proven here by pointing repo_root at a directory with no
    scripts/sync-state.sh at all; running it would raise, not merely 500."""

    with tempfile.TemporaryDirectory(prefix="dashboard-governed-refresh-") as tmpdir:
        root = Path(tmpdir)
        (root / ".orchestrator").mkdir()
        (root / ".orchestrator" / "config.json").write_text(
            '{"task_state_store": {"mode": "authoritative", "event_log": "x"}}',
            encoding="utf-8",
        )
        dashboard_server.NoCacheRequestHandler.repo_root = root
        handler = functools.partial(
            dashboard_server.NoCacheRequestHandler,
            directory=str(root),
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/__refresh", method="POST"
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200
                payload = json.loads(response.read())
            assert payload["ok"] is True
            assert "journal-governed" in payload["stdout"]
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


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
