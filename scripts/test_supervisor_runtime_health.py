from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supervisor_runtime_health import evaluate_runtime_health


NOW = datetime(2026, 8, 11, 18, 30, tzinfo=timezone.utc)
COMMIT = "a" * 40
PID = 4321
STARTTIME = 987654


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def healthy_fixture(repo: Path) -> dict[str, Any]:
    status_root = repo / "status-root"
    runtime_root = repo / "command-runtime"
    runtime_root.mkdir(parents=True)
    state_path = status_root / ".orchestrator" / "state.json"
    status_path = status_root / "ai-status.json"
    approval_queue_path = status_root / ".orchestrator" / "approval-queue.json"
    event_log = repo / "runtime" / "task-state-events.jsonl"
    head_path = event_log.with_name(f"{event_log.name}.head.json")
    config_path = repo / "runtime" / "live-config.json"
    argv = (
        "/usr/bin/python3",
        "-u",
        "-B",
        str(runtime_root / ".orchestrator" / "supervisor.py"),
        "--config",
        str(config_path),
        "--verbose",
    )
    config = {
        "paths": {
            "state_file": str(state_path),
            "status_file": str(status_path),
            "approval_queue": str(approval_queue_path),
        },
        "task_state_store": {"mode": "authoritative", "event_log": str(event_log)},
        "watchdog": {
            "heartbeat_stale_seconds": 120,
            "state_file": str(status_root / ".orchestrator" / "watchdog-state.json"),
            "contention_metrics_file": str(status_root / ".orchestrator" / "watchdog-contention.jsonl"),
            "supervisor_command": list(argv),
        },
        "supervisor": {
            "poll_interval_seconds": 30,
            "stall_after_seconds": 120,
            "cycle_budget_seconds": 60,
            "dispatch_latency_budget_seconds": 45,
        },
        "providers": {"codex": {"enabled": True}},
    }
    state = {
        "supervisor": {
            "pid": PID,
            "last_heartbeat_at": "2026-08-11T18:29:50Z",
            "last_loop_started_at": "2026-08-11T18:29:40Z",
            "last_loop_finished_at": "2026-08-11T18:29:50Z",
            "last_successful_loop_at": "2026-08-11T18:29:50Z",
            "last_loop_error": None,
            "lifecycle": "running",
            "last_cycle_metrics": {
                "cycle_elapsed_seconds": 10.0,
                "queue_to_start": {"count": 1, "average_seconds": 2.0, "max_seconds": 2.0},
            },
            "task_state_projection": {
                "mode": "authoritative",
                "ok": True,
                "caught_up": True,
                "last_error": None,
                "projected_state_sha256": "projection-sha",
                "expected_state_sha256": "projection-sha",
            },
        },
        "workers": {
            "run-1": {
                "status": "running",
                "current_task_id": "TASK-1",
                "queue_event_id": "evt-1",
            }
        },
        "queue": {
            "version": 2,
            "events": {
                "evt-1": {
                    "intent": {"event_id": "evt-1", "task_id": "TASK-1"},
                    "status": "started",
                }
            }
        },
        "worker_worktrees": {"leases": {"TASK-1": {"task_id": "TASK-1"}}},
    }
    status = {
        "tasks": [
            {
                "id": "TASK-1",
                "status": "in_progress",
                "owner": "Codex",
                "depends_on": [],
            }
        ]
    }
    process = {
        "pid": PID,
        "starttime_ticks": STARTTIME,
        "state": "S",
        "argv": argv,
        "cwd": str(runtime_root),
        "environment": {
            "PANTHEON_COMMAND_ROOT": str(runtime_root),
            "PANTHEON_COMMAND_RUNTIME_SHA": COMMIT,
            "PANTHEON_STATUS_ROOT": str(status_root),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        "singleton_owner_pid": PID,
        "singleton_owner_starttime_ticks": STARTTIME,
    }
    write_json(config_path, config)
    write_json(state_path, state)
    write_json(status_path, status)
    write_json(approval_queue_path, {"version": 2, "pending": [], "history": []})
    write_json(head_path, {"sequence": 7, "state": status})
    state_path.with_name("supervisor.pid").write_text(f"{PID}\n", encoding="utf-8")
    return {
        "config": config,
        "config_path": config_path,
        "state": state,
        "state_path": state_path,
        "status": status,
        "status_path": status_path,
        "approval_queue_path": approval_queue_path,
        "runtime_root": runtime_root,
        "process": process,
        "status_root": status_root,
    }


def evaluate(repo: Path, fixture: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    kwargs = {
        "config_path_arg": fixture["config_path"],
        "now": NOW,
        "expected_command_root": fixture["runtime_root"],
        "expected_source_commit": COMMIT,
        "expected_config_sha256": hashlib.sha256(fixture["config_path"].read_bytes()).hexdigest(),
        "expected_process_generation": (PID, STARTTIME),
        "verified_runtime_identity": fixture["process"],
    }
    kwargs.update(overrides)
    return evaluate_runtime_health(repo, **kwargs)


def failed_checks(report: dict[str, Any]) -> set[str]:
    return {item["name"] for item in report["checks"] if not item["ok"]}


def test_health_reports_four_independent_healthy_dimensions(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)

    report = evaluate(tmp_path, fixture)

    assert report["healthy"] is True
    assert set(report["dimensions"]) == {"identity", "liveness", "readiness", "progress"}
    assert all(dimension["healthy"] for dimension in report["dimensions"].values())
    assert report["identity"]["pid_generation"] == [PID, STARTTIME]


def test_held_singleton_cannot_compensate_for_wrong_process_generation(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    fixture["process"]["starttime_ticks"] = STARTTIME + 1
    fixture["process"]["singleton_owner_starttime_ticks"] = STARTTIME + 1

    report = evaluate(tmp_path, fixture)

    assert report["healthy"] is False
    assert report["dimensions"]["identity"]["healthy"] is False
    assert report["dimensions"]["liveness"]["healthy"] is False
    assert "identity_pid_generation_exact" in failed_checks(report)
    assert "liveness_exact_process_generation_alive" in failed_checks(report)


def test_live_process_cannot_compensate_for_orphaned_singleton_lock(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    fixture["process"]["singleton_owner_pid"] = PID + 1

    report = evaluate(tmp_path, fixture)

    assert report["dimensions"]["identity"]["healthy"] is True
    assert report["dimensions"]["liveness"]["healthy"] is False
    assert "liveness_singleton_owned_by_generation" in failed_checks(report)


def test_fresh_identity_and_lock_do_not_compensate_for_stale_heartbeat(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    fixture["state"]["supervisor"]["last_heartbeat_at"] = "2026-08-11T18:00:00Z"
    write_json(fixture["state_path"], fixture["state"])

    report = evaluate(tmp_path, fixture)

    assert report["dimensions"]["identity"]["healthy"] is True
    assert report["dimensions"]["liveness"]["healthy"] is True
    assert report["dimensions"]["progress"]["healthy"] is False
    assert "progress_heartbeat_fresh" in failed_checks(report)


def test_health_does_not_reimplement_dispatch_admission(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    fixture["state"]["workers"] = {}
    fixture["state"]["queue"] = {"version": 2, "events": {}}
    write_json(fixture["state_path"], fixture["state"])

    report = evaluate(tmp_path, fixture)

    assert report["dimensions"]["progress"]["healthy"] is True
    assert report["healthy"] is True


def test_health_accepts_fresh_success_while_next_cycle_is_in_flight(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    fixture["state"]["supervisor"].update(
        {
            "last_heartbeat_at": "2026-08-11T18:29:55Z",
            "last_loop_started_at": "2026-08-11T18:29:55Z",
            "last_loop_finished_at": "2026-08-11T18:29:50Z",
            "last_successful_loop_at": "2026-08-11T18:29:50Z",
            "last_loop_error": None,
        }
    )
    write_json(fixture["state_path"], fixture["state"])

    report = evaluate(tmp_path, fixture)

    assert report["dimensions"]["progress"]["healthy"] is True
    progress = next(
        item
        for item in report["checks"]
        if item["name"] == "progress_fresh_successful_loop"
    )
    assert progress["in_flight_after_success"] is True


def test_health_rejects_an_in_flight_cycle_that_exceeds_its_budget(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    fixture["state"]["supervisor"].update(
        {
            "last_heartbeat_at": "2026-08-11T18:29:55Z",
            "last_loop_started_at": "2026-08-11T18:28:00Z",
            "last_loop_finished_at": "2026-08-11T18:27:59Z",
            "last_successful_loop_at": "2026-08-11T18:27:59Z",
            "last_loop_error": None,
        }
    )
    write_json(fixture["state_path"], fixture["state"])

    report = evaluate(tmp_path, fixture)

    assert report["dimensions"]["progress"]["healthy"] is False
    assert "progress_cycle_within_budget" in failed_checks(report)


def test_wrong_runtime_root_fails_identity_even_when_process_is_live(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    fixture["process"]["environment"]["PANTHEON_COMMAND_ROOT"] = str(tmp_path / "wrong-root")

    report = evaluate(tmp_path, fixture)

    assert report["dimensions"]["identity"]["healthy"] is False
    assert "identity_command_root_exact" in failed_checks(report)


def test_readiness_requires_worker_coordination_marker(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    fixture["approval_queue_path"].unlink()

    report = evaluate(tmp_path, fixture)

    assert report["dimensions"]["readiness"]["healthy"] is False
    assert "readiness_approval_queue_marker_accessible" in failed_checks(report)


def test_require_watchdog_accepts_fresh_lock_contention_probe(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    watchdog_path = Path(fixture["config"]["watchdog"]["state_file"])
    contention_path = Path(fixture["config"]["watchdog"]["contention_metrics_file"])
    write_json(watchdog_path, {"updated_at": "2026-08-11T18:00:00Z"})
    contention_path.write_text(
        json.dumps(
            {
                "version": 1,
                "at": "2026-08-11T18:29:40Z",
                "decision": "skip",
                "reason": "lock_contention",
                "lock_held": True,
            }
        )
        + "\n{partial",
        encoding="utf-8",
    )

    report = evaluate(tmp_path, fixture, require_watchdog=True, max_watchdog_age=180)

    assert report["healthy"] is True
    assert report["watchdog"]["probe_source"] == "contention_metric"


def test_require_watchdog_rejects_untrusted_contention_probe(tmp_path: Path) -> None:
    fixture = healthy_fixture(tmp_path)
    watchdog_path = Path(fixture["config"]["watchdog"]["state_file"])
    contention_path = Path(fixture["config"]["watchdog"]["contention_metrics_file"])
    write_json(watchdog_path, {"updated_at": "2026-08-11T18:00:00Z"})
    contention_path.write_text(
        json.dumps(
            {
                "version": 1,
                "at": "2026-08-11T18:29:40Z",
                "decision": "skip",
                "reason": "lock_contention",
                "lock_held": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = evaluate(tmp_path, fixture, require_watchdog=True, max_watchdog_age=180)

    assert report["healthy"] is False
    assert "progress_watchdog_probe_fresh" in failed_checks(report)
