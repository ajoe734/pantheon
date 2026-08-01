from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from unittest.mock import patch

from promote_supervisor_runtime import (
    capture_promotion_snapshot,
    evaluate_promotion_invariants,
)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def create_realistic_healthy_fixture(repo: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = {
        "paths": {
            "state_file": ".orchestrator/state.json",
            "status_file": "ai-status.json",
            "provider_capabilities": ".orchestrator/provider_capabilities.json",
        },
        "watchdog": {"heartbeat_stale_seconds": 900},
        "supervisor": {"stall_after_seconds": 900},
        "providers": {
            "claude": {"enabled": True},
            "gemini": {"enabled": True},
        },
    }
    state = {
        "supervisor": {
            "last_heartbeat_at": "2026-06-06T06:29:30Z",
            "last_loop_started_at": "2026-06-06T06:29:00Z",
            "last_loop_finished_at": "2026-06-06T06:29:30Z",
            "last_successful_loop_at": "2026-06-06T06:29:30Z",
            "last_loop_error": None,
            "lifecycle": "running",
            "pid": 12345,
            "task_state_shadow": {
                "mode": "authoritative",
                "ok": True,
                "caught_up": True,
                "last_error": None,
                "projected_state_sha256": "abc123sha",
                "expected_state_sha256": "abc123sha",
            },
        },
        "workers": {
            "w1": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run1"}
        },
        "queue": {
            "events": {"evt1": {"id": "evt1", "task_id": "T1", "worker": "w1", "run_id": "run1", "lease_owner": "run1"}}
        },
        "worker_worktrees": {
            "leases": {
                "w1_lease": {"task_id": "T1", "branch": "task/T1", "queue_event_id": "evt1", "run_id": "run1"}
            }
        },
    }
    ai_status = {
        "sprint": "test-sprint",
        "tasks": [{"id": "T1", "status": "in_progress", "owner": "Codex"}],
        "agents": [],
    }
    provider_capabilities = {
        "generated_at": "2026-06-06T06:00:00Z",
        "providers": {
            "claude": {"auth_ready": True, "local_cli_worker_supported": True},
            "gemini": {"auth_ready": True, "local_cli_worker_supported": True},
        },
    }

    write_json(repo / ".orchestrator" / "config.json", config)
    write_json(repo / ".orchestrator" / "state.json", state)
    (repo / ".orchestrator" / "supervisor.pid").write_text("12345\n", encoding="utf-8")
    write_json(repo / "ai-status.json", ai_status)
    write_json(repo / ".orchestrator" / "provider_capabilities.json", provider_capabilities)

    return config, state, ai_status, provider_capabilities


@patch("promote_supervisor_runtime.lock_held", return_value=True)
@patch("promote_supervisor_runtime.pid_is_alive", return_value=True)
@patch("supervisor_runtime_health.lock_held", return_value=True)
@patch("supervisor_runtime_health.pid_matches_supervisor", return_value=True)
def test_promotion_snapshot_eligible_when_healthy(mock_matches, mock_sup_lock, mock_alive, mock_lock, tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    create_realistic_healthy_fixture(repo)

    snapshot = capture_promotion_snapshot(repo, now=now, skip_identity_guards=True)

    assert snapshot["eligible_for_promotion"] is True
    assert len(snapshot["file_errors"]) == 0
    assert all(inv["ok"] for inv in snapshot["invariants"])


def test_capture_promotion_snapshot_fail_closed_on_missing_files(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    snapshot = capture_promotion_snapshot(repo, now=now)

    assert snapshot["eligible_for_promotion"] is False
    assert len(snapshot["file_errors"]) > 0
    inv = next(i for i in snapshot["invariants"] if i["name"] == "config_and_state_files_readable")
    assert inv["ok"] is False


@patch("promote_supervisor_runtime.lock_held", return_value=True)
@patch("promote_supervisor_runtime.pid_is_alive", return_value=True)
def test_evaluate_promotion_invariants_healthy(mock_alive, mock_lock, tmp_path: Path) -> None:
    repo = tmp_path
    config, state, ai_status, provider_capabilities = create_realistic_healthy_fixture(repo)
    health_report = {
        "healthy": True,
        "supervisor": state["supervisor"],
        "checks": [{"name": "supervisor_process_alive", "ok": True}],
    }

    invariants = evaluate_promotion_invariants(
        health_report=health_report,
        ai_status=ai_status,
        state=state,
        provider_capabilities=provider_capabilities,
        lock_path=Path("/tmp/fake.lock"),
        now=datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc),
        config=config,
    )

    assert all(inv["ok"] for inv in invariants)


def test_evaluate_promotion_invariants_detects_pid_unbound_or_unlocked() -> None:
    health_report = {
        "healthy": True,
        "supervisor": {"lifecycle": "running", "pid": 12345},
    }
    ai_status = {"tasks": []}
    state = {}

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=False):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    pid_inv = next(i for i in invariants if i["name"] == "supervisor_pid_bound_and_locked")
    assert pid_inv["ok"] is False


def test_evaluate_promotion_invariants_detects_invalid_task_state_shadow() -> None:
    health_report = {
        "healthy": True,
        "supervisor": {
            "lifecycle": "running",
            "pid": 12345,
            "task_state_shadow": {
                "mode": "shadow",  # Not authoritative
                "ok": True,
                "caught_up": False,  # Not caught up
                "last_error": "Diverged",
            },
        },
    }
    ai_status = {"tasks": []}
    state = {}

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    shadow_inv = next(i for i in invariants if i["name"] == "task_state_shadow_valid")
    assert shadow_inv["ok"] is False
    reasons = shadow_inv["details"]["reasons"]
    assert "mode_not_authoritative:shadow" in reasons
    assert "caught_up_not_true:False" in reasons
    assert "has_last_error:Diverged" in reasons


def test_evaluate_promotion_invariants_detects_missing_task_state_shadow() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 12345}}
    ai_status = {"tasks": []}
    state = {}

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    shadow_inv = next(i for i in invariants if i["name"] == "task_state_shadow_valid")
    assert shadow_inv["ok"] is False
    assert "task_state_shadow_missing" in shadow_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_detects_fresh_loop_sequence_failures() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 12345}}
    ai_status = {"tasks": []}
    # State missing last_successful_loop_at and having last_loop_error
    state = {
        "supervisor": {
            "last_loop_started_at": "2026-06-06T06:00:00Z",
            "last_loop_finished_at": "2026-06-06T06:00:10Z",
            "last_successful_loop_at": None,
            "last_loop_error": "TimeoutError: loop exceeded 300s",
        }
    }

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state=state,
            lock_path=Path("/tmp/fake.lock"),
            now=datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc),
            config={"supervisor": {"stall_after_seconds": 900}},
        )

    loop_inv = next(i for i in invariants if i["name"] == "fresh_loop_sequence")
    assert loop_inv["ok"] is False
    reasons = loop_inv["details"]["reasons"]
    assert "missing_last_successful_loop_at" in reasons
    assert "has_last_loop_error:TimeoutError: loop exceeded 300s" in reasons


def test_evaluate_promotion_invariants_detects_worker_lease_missing() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    ai_status = {"tasks": []}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w1": {"status": "running", "current_task_id": "T1"},
        },
        "queue": {"events": {}},
        "worker_worktrees": {"leases": {}},  # Empty leases!
    }

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "active_worker_missing_lease:w1:T1" in worker_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_detects_duplicate_active_workers() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    ai_status = {"tasks": []}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w1": {"status": "running", "current_task_id": "T1"},
            "w2": {"status": "running", "current_task_id": "T1"},
        },
        "worker_worktrees": {
            "leases": {
                "l1": {"task_id": "T1"}
            }
        },
    }

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "w2:T1" in worker_inv["details"]["duplicate_active_workers"]


def test_evaluate_promotion_invariants_detects_unready_provider_capabilities() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    ai_status = {"tasks": []}
    config = {
        "providers": {
            "claude": {"enabled": True},
        }
    }
    provider_capabilities = {
        "providers": {
            "claude": {"auth_ready": False, "local_cli_worker_supported": True}
        }
    }
    state = {
        "workers": {
            "w1": {"status": "running", "provider": "claude", "current_task_id": "T1"}
        }
    }

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state=state,
            provider_capabilities=provider_capabilities,
            lock_path=Path("/tmp/fake.lock"),
            config=config,
        )

    provider_inv = next(i for i in invariants if i["name"] == "provider_readiness_baseline")
    assert provider_inv["ok"] is False
    assert "provider_auth_not_ready:claude" in provider_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_detects_missing_shadow_hashes() -> None:
    health_report = {
        "healthy": True,
        "supervisor": {
            "lifecycle": "running",
            "pid": 12345,
            "task_state_shadow": {
                "mode": "authoritative",
                "ok": True,
                "caught_up": True,
                "last_error": None,
                "projected_state_sha256": "",
                "expected_state_sha256": "",
            },
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state={},
            lock_path=Path("/tmp/fake.lock"),
        )

    shadow_inv = next(i for i in invariants if i["name"] == "task_state_shadow_valid")
    assert shadow_inv["ok"] is False
    reasons = shadow_inv["details"]["reasons"]
    assert "missing_projected_state_sha256" in reasons
    assert "missing_expected_state_sha256" in reasons


def test_evaluate_promotion_invariants_detects_orphan_active_lease_and_queue() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {},
        "queue": {
            "events": {
                "e1": {"id": "e1", "task_id": "T1", "status": "pending", "worker": "unknown_worker", "lease_owner": "run_unassigned"},
            }
        },
        "worker_worktrees": {
            "leases": {
                "l1": {"task_id": "T2", "status": "active"}
            }
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    reasons = worker_inv["details"]["reasons"]
    assert "orphan_active_lease:T2" in reasons
    assert "active_queue_event_missing_worker:e1:T1" in reasons


def test_evaluate_promotion_invariants_accepts_inactive_unavailable_provider() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    config = {
        "providers": {
            "claude": {"enabled": True},
            "inactive_provider": {"enabled": True},
        },
        "ready_dispatcher": {
            "target_workload": {"claude": 5},
            "disabled_agents": ["inactive_provider"],
        },
    }
    provider_capabilities = {
        "providers": {
            "claude": {"auth_ready": True, "local_cli_worker_supported": True},
            "inactive_provider": {"auth_ready": False, "local_cli_worker_supported": False},
        }
    }

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state={"workers": {"w1": {"status": "running", "provider": "claude", "current_task_id": "T1"}}, "worker_worktrees": {"leases": {"l1": {"task_id": "T1"}}}},
            provider_capabilities=provider_capabilities,
            lock_path=Path("/tmp/fake.lock"),
            config=config,
        )

    provider_inv = next(i for i in invariants if i["name"] == "provider_readiness_baseline")
    assert provider_inv["ok"] is True


def test_evaluate_promotion_invariants_detects_orphaned_task() -> None:
    health_report = {
        "healthy": True,
        "supervisor": {"lifecycle": "running", "pid": 123},
        "checks": [{"name": "supervisor_process_alive", "ok": True}],
    }
    ai_status = {
        "tasks": [{"id": "T1", "status": "in_progress", "owner": ""}]
    }

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state={},
            lock_path=Path("/tmp/fake.lock"),
        )

    orphan_inv = next(i for i in invariants if i["name"] == "no_orphaned_in_progress_tasks")
    assert orphan_inv["ok"] is False
    assert orphan_inv["details"]["orphaned_tasks"] == ["T1"]


def test_evaluate_promotion_invariants_detects_active_queue_event_without_worker() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {},
        "queue": {
            "events": {
                "evt_no_worker": {"id": "evt_no_worker", "task_id": "T1", "status": "running", "lease_owner": "run_unassigned"}
            }
        },
        "worker_worktrees": {"leases": {}},
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "active_queue_event_missing_worker:evt_no_worker:T1" in worker_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_accepts_event_omitting_worker_with_valid_reverse_linked_worker() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w1": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run1"}
        },
        "queue": {
            "events": {
                "evt1": {"id": "evt1", "task_id": "T1", "status": "running", "run_id": "run1", "lease_owner": "run1"}  # Omits worker/assigned_worker
            }
        },
        "worker_worktrees": {
            "leases": {
                "l1": {"task_id": "T1", "queue_event_id": "evt1", "run_id": "run1"}
            }
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is True


def test_evaluate_promotion_invariants_detects_multiple_reverse_linked_workers_for_event() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w1": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run_shared"},
            "w2": {"status": "running", "current_task_id": "T2", "queue_event_id": "evt1", "run_id": "run_shared"},
        },
        "queue": {
            "events": {
                "evt1": {"id": "evt1", "task_id": "T1", "status": "running", "lease_owner": "run_shared"}
            }
        },
        "worker_worktrees": {
            "leases": {
                "l1": {"task_id": "T1", "queue_event_id": "evt1", "run_id": "run_shared"},
                "l2": {"task_id": "T2", "queue_event_id": "evt1", "run_id": "run_shared"},
            }
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "active_queue_event_multiple_workers:evt1:['w1', 'w2']" in worker_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_accepts_initial_attempt_and_retry_lineage() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w_initial": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt_init", "run_id": "run_init"},
            "w_history_node": {"status": "completed", "current_task_id": "T2", "queue_event_id": "evt_retry", "run_id": "run_retry_1"},
            "w_retry": {"status": "running", "current_task_id": "T2", "queue_event_id": "evt_retry", "run_id": "run_retry_2", "parent_run_id": "run_retry_1"},
        },
        "queue": {
            "events": {
                "evt_init": {"id": "evt_init", "task_id": "T1", "status": "running", "run_id": "run_init", "lease_owner": "run_init"},
                "evt_retry": {"id": "evt_retry", "task_id": "T2", "status": "running", "run_id": "run_retry_1", "lease_owner": "run_retry_2"},
            }
        },
        "worker_worktrees": {
            "leases": {
                "l1": {"task_id": "T1", "queue_event_id": "evt_init", "run_id": "run_init"},
                "l2": {"task_id": "T2", "queue_event_id": "evt_retry", "run_id": "run_retry_2"},
            }
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is True


def test_evaluate_promotion_invariants_rejects_missing_lease_owner() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w1": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run1"},
        },
        "queue": {
            "events": {
                "evt1": {"id": "evt1", "task_id": "T1", "status": "running", "run_id": "run1"}  # missing lease_owner!
            }
        },
        "worker_worktrees": {
            "leases": {"l1": {"task_id": "T1", "queue_event_id": "evt1", "run_id": "run1"}}
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "missing_lease_owner:evt1" in worker_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_rejects_nonexistent_history() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w_retry": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run_retry", "parent_run_id": "run_missing"},
        },
        "queue": {
            "events": {
                "evt1": {"id": "evt1", "task_id": "T1", "status": "running", "run_id": "run_missing", "lease_owner": "run_retry"}
            }
        },
        "worker_worktrees": {
            "leases": {"l1": {"task_id": "T1", "queue_event_id": "evt1", "run_id": "run_retry"}}
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "missing_history:evt1:run_id_run_missing_not_found" in worker_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_rejects_cross_task_and_cross_event_lineage() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w_other_task": {"status": "completed", "current_task_id": "T_OTHER", "queue_event_id": "evt_other", "run_id": "run_other"},
            "w_retry": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run_retry", "parent_run_id": "run_other"},
            "w_init": {"status": "completed", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run_init"},
        },
        "queue": {
            "events": {
                "evt1": {"id": "evt1", "task_id": "T1", "status": "running", "run_id": "run_init", "lease_owner": "run_retry"}
            }
        },
        "worker_worktrees": {
            "leases": {"l1": {"task_id": "T1", "queue_event_id": "evt1", "run_id": "run_retry"}}
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "cross_task_retry_lineage:run_retry:T_OTHER!=T1" in worker_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_rejects_cycle_in_retry_lineage() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w1": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run1", "parent_run_id": "run2"},
            "w2": {"status": "completed", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run2", "parent_run_id": "run1"},
        },
        "queue": {
            "events": {
                "evt1": {"id": "evt1", "task_id": "T1", "status": "running", "run_id": "run_target", "lease_owner": "run1"}
            }
        },
        "worker_worktrees": {
            "leases": {"l1": {"task_id": "T1", "queue_event_id": "evt1", "run_id": "run1"}}
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    reasons = worker_inv["details"]["reasons"]
    assert any("cycle_in_retry_lineage" in r or "missing_history" in r for r in reasons)


def test_evaluate_promotion_invariants_rejects_target_cross_task_and_cross_event() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w_retry": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run_retry", "parent_run_id": "run_target_cross"},
            "w_target_cross": {"status": "completed", "current_task_id": "T_OTHER", "queue_event_id": "evt1", "run_id": "run_target_cross"},
        },
        "queue": {
            "events": {
                "evt1": {"id": "evt1", "task_id": "T1", "status": "running", "run_id": "run_target_cross", "lease_owner": "run_retry"}
            }
        },
        "worker_worktrees": {
            "leases": {"l1": {"task_id": "T1", "queue_event_id": "evt1", "run_id": "run_retry"}}
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "cross_task_retry_lineage:run_retry:T_OTHER!=T1" in worker_inv["details"]["reasons"]


def test_evaluate_promotion_invariants_rejects_duplicate_canonical_run_id() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w1": {"status": "completed", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run_dup"},
            "w2": {"status": "completed", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run_dup"},
            "w_active": {"status": "running", "current_task_id": "T1", "queue_event_id": "evt1", "run_id": "run_active", "parent_run_id": "run_dup"},
        },
        "queue": {
            "events": {
                "evt1": {"id": "evt1", "task_id": "T1", "status": "running", "run_id": "run_dup", "lease_owner": "run_active"}
            }
        },
        "worker_worktrees": {
            "leases": {"l1": {"task_id": "T1", "queue_event_id": "evt1", "run_id": "run_active"}}
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is False
    assert "duplicate_canonical_run_id:run_dup" in worker_inv["details"]["reasons"]


def test_validate_candidate_root_out_of_prefix_and_symlink(tmp_path: Path) -> None:
    from promote_supervisor_runtime import validate_candidate_root

    # Out of prefix
    info = validate_candidate_root(tmp_path)
    assert info["ok"] is False
    assert any("out_of_prefix_root" in r for r in info["reasons"])

    # Symlink check
    symlink_path = tmp_path / "link"
    try:
        symlink_path.symlink_to(tmp_path)
        info_sym = validate_candidate_root(symlink_path)
        assert info_sym["ok"] is False
        assert any("candidate_root_is_symlink" in r for r in info_sym["reasons"])
    except OSError:
        pass


def test_validate_git_identity_wrong_remote_and_dirty_tree(tmp_path: Path) -> None:
    from promote_supervisor_runtime import validate_git_identity

    info = validate_git_identity(tmp_path)
    assert info["ok"] is False
    assert any("git_get_url_origin_failed" in r or "wrong_remote" in r for r in info["reasons"])


def test_discover_incumbent_supervisor_process_stale_or_missing(tmp_path: Path) -> None:
    from promote_supervisor_runtime import discover_incumbent_supervisor_process

    info = discover_incumbent_supervisor_process(tmp_path, health_report={"supervisor": {"pid": 999999}})
    assert info["ok"] is False
    assert "no_alive_supervisor_pid" in info["reasons"]


def test_evaluate_governed_launch_contract(tmp_path: Path) -> None:
    from promote_supervisor_runtime import evaluate_governed_launch_contract

    info = evaluate_governed_launch_contract(tmp_path)
    assert "governed_interpreter" in info
    assert "governed_cwd" in info
    assert "log_file" in info
    assert info["scrubbed_env_keys"] == ["PANTHEON_STATUS_ROOT", "PANTHEON_COMMAND_ROOT", "PANTHEON_COMMAND_RUNTIME_SHA", "PATH", "PYTHONPATH"]


def test_config_bytes_identity_capture_and_revalidation(tmp_path: Path) -> None:
    from promote_supervisor_runtime import capture_config_bytes_identity, revalidate_config_bytes_identity

    cfg = tmp_path / "config.json"
    cfg.write_text('{"foo": "bar"}', encoding="utf-8")

    info = capture_config_bytes_identity(cfg)
    assert info["ok"] is True
    sha = info["sha256"]
    assert info["byte_length"] == len('{"foo": "bar"}')

    # Revalidation success
    reval = revalidate_config_bytes_identity(cfg, expected_sha256=sha)
    assert reval["ok"] is True

    # Revalidation failure on mismatch
    reval_bad = revalidate_config_bytes_identity(cfg, expected_sha256="0000000000000000000000000000000000000000000000000000000000000000")
    assert reval_bad["ok"] is False
    assert any("config_sha256_mismatch" in r for r in reval_bad["reasons"])


def test_discover_only_mode_rejects_non_prefix_worktree(tmp_path: Path) -> None:
    from promote_supervisor_runtime import validate_candidate_root

    info = validate_candidate_root(tmp_path, discover_only=True)
    assert info["ok"] is False
    assert "discover_only_preflight_failed" in info["reasons"]


