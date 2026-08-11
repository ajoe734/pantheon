from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path

import supervisor_runtime_health as health
from supervisor_runtime_health import evaluate_runtime_health


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def configure_exact_runtime(repo: Path, monkeypatch: object) -> None:
    config_path = repo / ".orchestrator" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("watchdog", {})["supervisor_command"] = [
        "/usr/bin/python3",
        str(repo / ".orchestrator" / "supervisor.py"),
        "--config",
        str(config_path),
    ]
    write_json(config_path, config)
    (repo / ".orchestrator" / "supervisor.pid").write_text("1234\n", encoding="utf-8")
    monkeypatch.setattr(health, "pid_is_alive", lambda pid: pid == 1234)
    monkeypatch.setattr(
        health,
        "pid_matches_supervisor",
        lambda pid, **_kwargs: pid == 1234,
    )


def test_health_passes_when_exact_runtime_lock_and_progress_are_fresh(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json"},
            "watchdog": {"heartbeat_stale_seconds": 900},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:30Z",
                "last_successful_loop_at": "2026-06-06T06:29:20Z",
                "lifecycle": "running",
                "last_loop_error": None,
            }
        },
    )
    configure_exact_runtime(repo, monkeypatch)
    lock_path = repo / ".orchestrator" / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        report = evaluate_runtime_health(repo, now=now)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert report["healthy"] is True
    assert report["supervisor"]["lock_held"] is True
    assert all(section["ok"] for section in report["dimensions"].values())


def test_health_fails_on_stale_heartbeat(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    write_json(repo / ".orchestrator" / "config.json", {"paths": {"state_file": ".orchestrator/state.json"}})
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:00:00Z",
                "last_successful_loop_at": "2026-06-06T06:00:00Z",
                "lifecycle": "running",
            }
        },
    )

    report = evaluate_runtime_health(repo, now=now, max_heartbeat_age=90)

    assert report["healthy"] is False
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert "supervisor_process_alive" in failed
    assert "supervisor_heartbeat_fresh" in failed


def test_require_watchdog_fails_when_probe_is_stale(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json"},
            "watchdog": {"state_file": ".orchestrator/watchdog-state.json"},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:50Z",
                "last_successful_loop_at": "2026-06-06T06:29:45Z",
                "lifecycle": "running",
            }
        },
    )
    configure_exact_runtime(repo, monkeypatch)
    write_json(repo / ".orchestrator" / "watchdog-state.json", {"updated_at": "2026-06-06T06:00:00Z"})
    lock_path = repo / ".orchestrator" / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        report = evaluate_runtime_health(repo, now=now, require_watchdog=True, max_watchdog_age=180)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert report["healthy"] is False
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert "watchdog_probe_fresh" in failed


def test_require_watchdog_accepts_fresh_lock_contention_probe(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json"},
            "watchdog": {
                "state_file": ".orchestrator/watchdog-state.json",
                "contention_metrics_file": ".orchestrator/metrics/watchdog-contention.jsonl",
            },
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:50Z",
                "last_successful_loop_at": "2026-06-06T06:29:45Z",
                "lifecycle": "running",
            }
        },
    )
    configure_exact_runtime(repo, monkeypatch)
    write_json(repo / ".orchestrator" / "watchdog-state.json", {"updated_at": "2026-06-06T06:00:00Z"})
    contention_path = repo / ".orchestrator" / "metrics" / "watchdog-contention.jsonl"
    contention_path.parent.mkdir(parents=True, exist_ok=True)
    contention_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "version": 1,
                        "at": "2026-06-06T06:29:40Z",
                        "decision": "skip",
                        "reason": "lock_contention",
                        "lock_held": True,
                    }
                ),
                "{partial",
            ]
        ),
        encoding="utf-8",
    )
    lock_path = repo / ".orchestrator" / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        report = evaluate_runtime_health(repo, now=now, require_watchdog=True, max_watchdog_age=180)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert report["healthy"] is True
    assert report["watchdog"]["probe_source"] == "contention_metric"
    assert report["watchdog"]["probe_updated_at"] == "2026-06-06T06:29:40Z"


def test_require_watchdog_rejects_untrusted_contention_probe(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json"},
            "watchdog": {"state_file": ".orchestrator/watchdog-state.json"},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:50Z",
                "last_successful_loop_at": "2026-06-06T06:29:45Z",
                "lifecycle": "running",
            }
        },
    )
    configure_exact_runtime(repo, monkeypatch)
    write_json(repo / ".orchestrator" / "watchdog-state.json", {"updated_at": "2026-06-06T06:00:00Z"})
    contention_path = repo / ".orchestrator" / "metrics" / "supervisor-watchdog-contention.jsonl"
    contention_path.parent.mkdir(parents=True, exist_ok=True)
    contention_path.write_text(
        json.dumps(
            {
                "version": 1,
                "at": "2026-06-06T06:29:40Z",
                "decision": "skip",
                "reason": "lock_contention",
                "lock_held": False,
            }
        ),
        encoding="utf-8",
    )
    lock_path = repo / ".orchestrator" / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        report = evaluate_runtime_health(repo, now=now, require_watchdog=True, max_watchdog_age=180)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert report["healthy"] is False
    failed = {item["name"] for item in report["checks"] if not item["ok"]}
    assert "watchdog_probe_fresh" in failed


def test_lock_without_exact_process_identity_is_not_healthy(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json"},
            "watchdog": {"heartbeat_stale_seconds": 900},
            "supervisor": {"stall_after_seconds": 900},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:50Z",
                "last_successful_loop_at": "2026-06-06T06:29:45Z",
                "lifecycle": "running",
                "last_loop_error": None,
            }
        },
    )
    lock_path = repo / ".orchestrator" / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = evaluate_runtime_health(repo, now=now)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert report["healthy"] is False
    assert report["dimensions"]["identity"]["ok"] is False
    assert report["supervisor"]["lock_held"] is True
    assert report["supervisor"]["process_alive"] is False


def test_fresh_heartbeat_does_not_hide_stalled_progress(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    write_json(
        repo / ".orchestrator" / "config.json",
        {
            "paths": {"state_file": ".orchestrator/state.json"},
            "watchdog": {"heartbeat_stale_seconds": 900},
            "supervisor": {"stall_after_seconds": 60},
        },
    )
    write_json(
        repo / ".orchestrator" / "state.json",
        {
            "supervisor": {
                "last_heartbeat_at": "2026-06-06T06:29:50Z",
                "last_successful_loop_at": "2026-06-06T06:00:00Z",
                "lifecycle": "running",
                "last_loop_error": None,
            }
        },
    )
    configure_exact_runtime(repo, monkeypatch)
    lock_path = repo / ".orchestrator" / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = evaluate_runtime_health(repo, now=now)
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

    assert report["healthy"] is False
    assert report["dimensions"]["identity"]["ok"] is True
    assert report["dimensions"]["liveness"]["ok"] is True
    assert report["dimensions"]["progress"]["ok"] is False
