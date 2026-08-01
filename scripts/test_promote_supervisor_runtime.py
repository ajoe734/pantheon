from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import patch

from promote_supervisor_runtime import (
    CandidateRuntimeIdentity,
    build_candidate_runtime_identity,
    capture_promotion_snapshot,
    evaluate_promotion_invariants,
    parse_origin_url,
    resolve_candidate_root,
    validate_remote_url,
    verify_git_head_and_dev_ancestry,
    verify_working_tree_cleanliness,
)
import promote_supervisor_runtime as promotion


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

    snapshot = capture_promotion_snapshot(repo, now=now)

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


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _make_candidate_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str, str, bytes]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--initial-branch=dev")
    _git(source, "config", "user.name", "Promotion Test")
    _git(source, "config", "user.email", "promotion@example.test")
    (source / "README.md").write_text("trusted candidate\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "trusted candidate")
    commit = _git(source, "rev-parse", "HEAD")
    tree = _git(source, "rev-parse", "HEAD^{tree}")

    trusted_remote = tmp_path / "trusted-remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(trusted_remote)],
        capture_output=True,
        check=True,
        text=True,
    )

    runtime_parent = tmp_path / "command-runtimes"
    runtime_parent.mkdir()
    candidate = runtime_parent / commit
    subprocess.run(
        ["git", "clone", "--quiet", str(source), str(candidate)],
        capture_output=True,
        check=True,
        text=True,
    )
    _git(
        candidate,
        "remote",
        "set-url",
        "origin",
        "https://github.com/ajoe734/pantheon.git",
    )

    config_bytes = b'{"runtime":"immutable","writes":false}\n'
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    live_config.parent.mkdir()
    live_config.write_bytes(config_bytes)
    return candidate, runtime_parent, trusted_remote, commit, tree, config_bytes


def _identity_policy_patches(
    runtime_parent: Path,
    trusted_remote: Path,
    live_config: Path,
):
    return (
        patch(
            "promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX",
            runtime_parent,
        ),
        patch(
            "promote_supervisor_runtime.TRUSTED_ORIGIN_DEV_URL",
            trusted_remote.as_uri(),
        ),
        patch(
            "promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH",
            live_config,
        ),
    )


def test_candidate_runtime_identity_captures_and_revalidates_exact_snapshot(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, commit, tree, config_bytes = _make_candidate_fixture(
        tmp_path
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        identity = build_candidate_runtime_identity(candidate)
        assert isinstance(identity, CandidateRuntimeIdentity)
        assert identity.candidate_root == candidate
        assert identity.basename == commit
        assert identity.head_commit == commit
        assert identity.tracked_tree_identity == tree
        assert identity.accepted_dev_commit == commit
        assert identity.canonical_remote == "github.com/ajoe734/pantheon"
        assert identity.repository_slug == "ajoe734/pantheon"
        assert identity.config_path == live_config
        assert identity.config_bytes == config_bytes
        assert identity.config_byte_length == len(config_bytes)
        assert len(identity.config_sha256) == 64
        identity.verify_against_live_config(live_config)
        identity.verify_immutable_snapshot()
        assert resolve_candidate_root(candidate) == candidate
        assert parse_origin_url(candidate) == "https://github.com/ajoe734/pantheon.git"
        assert verify_git_head_and_dev_ancestry(candidate, commit) == commit
        assert verify_working_tree_cleanliness(candidate) == tree


@pytest.mark.parametrize(
    "remote_url",
    [
        "https://github.com/ajoe734/pantheon.git",
        "https://github.com/ajoe734/pantheon",
        "git@github.com:ajoe734/pantheon.git",
        "git@github.com:ajoe734/pantheon",
        "ssh://git@github.com/ajoe734/pantheon.git",
    ],
)
def test_remote_identity_structural_parser_accepts_trusted_forms(
    remote_url: str,
) -> None:
    identity = validate_remote_url(remote_url)
    assert identity.host == "github.com"
    assert identity.slug == "ajoe734/pantheon"


@pytest.mark.parametrize(
    "remote_url",
    [
        "http://github.com/ajoe734/pantheon.git",
        "https://github.com/ajoe734/pantheon-shadow.git",
        "https://github.com/evil/ajoe734/pantheon.git",
        "https://github.com/ajoe734/pantheon.git/extra",
        "https://user@github.com/ajoe734/pantheon.git",
        "https://github.com:443/ajoe734/pantheon.git",
        "https://github.com/ajoe734/pantheon.git?mirror=evil",
        "git@example.invalid:evil/ajoe734/pantheon-shadow.git",
        "git@github.com:ajoe734/pantheon.git.evil",
        " git@github.com:ajoe734/pantheon.git",
    ],
)
def test_remote_identity_structural_parser_rejects_spoofs(remote_url: str) -> None:
    with pytest.raises(ValueError, match="Invalid/untrusted remote origin URL"):
        validate_remote_url(remote_url)


def test_candidate_root_deny_matrix(tmp_path: Path) -> None:
    candidate, parent, remote, commit, _tree, _config_bytes = _make_candidate_fixture(
        tmp_path
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        with pytest.raises(FileNotFoundError):
            resolve_candidate_root(parent / ("f" * 40))

        nested = parent / "nested" / ("e" * 40)
        nested.mkdir(parents=True)
        with pytest.raises(ValueError, match="not a direct child"):
            resolve_candidate_root(nested)

        with pytest.raises(ValueError, match="not a direct child"):
            resolve_candidate_root(parent / ".." / ("d" * 40))

        wrong_basename = parent / ("b" * 40)
        candidate.rename(wrong_basename)
        with pytest.raises(ValueError, match="does not match HEAD commit"):
            build_candidate_runtime_identity(wrong_basename)
        wrong_basename.rename(candidate)

        symlink_candidate = parent / ("c" * 40)
        symlink_candidate.symlink_to(candidate, target_is_directory=True)
        with pytest.raises(ValueError, match="symlink component"):
            resolve_candidate_root(symlink_candidate)

    parent_alias = tmp_path / "command-runtimes-alias"
    parent_alias.symlink_to(parent, target_is_directory=True)
    with patch(
        "promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX",
        parent_alias,
    ):
        with pytest.raises(ValueError, match="symlink component"):
            resolve_candidate_root(parent_alias / commit)


def test_candidate_root_rejects_tmp_and_worker_worktree_prefixes(
    tmp_path: Path,
) -> None:
    trusted_parent = tmp_path / "command-runtimes"
    trusted_parent.mkdir()
    tmp_candidate = tmp_path / ("a" * 40)
    tmp_candidate.mkdir()
    worker_candidate = (
        tmp_path
        / "pantheon-worker-worktrees"
        / "pantheon"
        / ("b" * 40)
    )
    worker_candidate.mkdir(parents=True)
    with patch(
        "promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX",
        trusted_parent,
    ):
        with pytest.raises(ValueError, match="not a direct child"):
            resolve_candidate_root(tmp_candidate)
        with pytest.raises(ValueError, match="not a direct child"):
            resolve_candidate_root(worker_candidate)


def test_candidate_root_deleted_during_capture_is_rejected(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = _make_candidate_fixture(
        tmp_path
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    original_capture = promotion._capture_git_identity

    def remove_after_git_capture(root: Path, basename: str):
        result = original_capture(root, basename)
        shutil.rmtree(root)
        return result

    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch, patch(
        "promote_supervisor_runtime._capture_git_identity",
        side_effect=remove_after_git_capture,
    ):
        with pytest.raises(FileNotFoundError, match="disappeared"):
            build_candidate_runtime_identity(candidate)


def test_git_identity_rejects_locally_forged_dev_and_unaccepted_commit(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = _make_candidate_fixture(
        tmp_path
    )
    _git(candidate, "config", "user.name", "Promotion Test")
    _git(candidate, "config", "user.email", "promotion@example.test")
    (candidate / "README.md").write_text("unaccepted candidate\n", encoding="utf-8")
    _git(candidate, "add", "README.md")
    _git(candidate, "commit", "-m", "not on trusted dev")
    unaccepted_commit = _git(candidate, "rev-parse", "HEAD")
    _git(candidate, "update-ref", "refs/remotes/origin/dev", unaccepted_commit)
    renamed_candidate = parent / unaccepted_commit
    candidate.rename(renamed_candidate)

    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        with pytest.raises(ValueError, match="cat-file"):
            build_candidate_runtime_identity(renamed_candidate)


def test_git_identity_rejects_candidate_tree_mismatch(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = _make_candidate_fixture(
        tmp_path
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    original_read = promotion._read_head_tree
    read_count = 0

    def forged_first_tree(root: Path) -> tuple[str, str]:
        nonlocal read_count
        read_count += 1
        head, tree = original_read(root)
        if read_count == 1:
            return head, "f" * 40
        return head, tree

    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch, patch(
        "promote_supervisor_runtime._read_head_tree",
        side_effect=forged_first_tree,
    ):
        with pytest.raises(ValueError, match="does not match the same commit"):
            build_candidate_runtime_identity(candidate)


@pytest.mark.parametrize("dirty_kind", ["unstaged", "staged", "deleted"])
def test_git_identity_rejects_every_tracked_dirty_state(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = _make_candidate_fixture(
        tmp_path
    )
    readme = candidate / "README.md"
    if dirty_kind == "deleted":
        readme.unlink()
    else:
        readme.write_text(f"{dirty_kind} change\n", encoding="utf-8")
        if dirty_kind == "staged":
            _git(candidate, "add", "README.md")

    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        with pytest.raises(ValueError, match="Tracked git tree is dirty"):
            build_candidate_runtime_identity(candidate)


def test_git_identity_allows_only_enumerated_generated_untracked_paths(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, tree, _config_bytes = _make_candidate_fixture(
        tmp_path
    )
    orchestrator = candidate / ".orchestrator"
    (orchestrator / "task-briefs").mkdir(parents=True)
    (orchestrator / "supervisor.lock").write_bytes(b"")
    (orchestrator / "status-derived-views.lock").write_bytes(b"")
    (orchestrator / "task-briefs" / "sup_runtime_identity_001.md").write_text(
        "generated task context\n",
        encoding="utf-8",
    )
    assert verify_working_tree_cleanliness(candidate) == tree

    forbidden_paths = [
        orchestrator / "config.json",
        orchestrator / "replacement.py",
        orchestrator / "task-briefs" / "replacement.py",
        candidate / "scripts" / "injected.py",
    ]
    for forbidden in forbidden_paths:
        forbidden.parent.mkdir(parents=True, exist_ok=True)
        forbidden.write_text("forbidden\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Forbidden untracked file"):
            verify_working_tree_cleanliness(candidate)
        forbidden.unlink()

    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        identity = build_candidate_runtime_identity(candidate)
        assert identity.tracked_tree_identity == tree


def test_live_config_rejects_external_and_symlink_alias_paths(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, config_bytes = _make_candidate_fixture(
        tmp_path
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    external_config = tmp_path / "runtime" / "other-config.json"
    external_config.write_bytes(config_bytes)
    config_alias = tmp_path / "runtime" / "config-alias.json"
    config_alias.symlink_to(live_config)

    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        with pytest.raises(ValueError, match="does not match exact live config path"):
            build_candidate_runtime_identity(candidate, config_path=external_config)
        with pytest.raises(ValueError, match="symlink component"):
            build_candidate_runtime_identity(candidate, config_path=config_alias)

    runtime_alias = tmp_path / "runtime-alias"
    runtime_alias.symlink_to(live_config.parent, target_is_directory=True)
    aliased_expected = runtime_alias / live_config.name
    with patch(
        "promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX",
        parent,
    ), patch(
        "promote_supervisor_runtime.TRUSTED_ORIGIN_DEV_URL",
        remote.as_uri(),
    ), patch(
        "promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH",
        aliased_expected,
    ):
        with pytest.raises(ValueError, match="symlink component"):
            build_candidate_runtime_identity(candidate)


def test_live_config_path_length_bytes_sha_and_inode_drift_are_independent(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, _tree, config_bytes = _make_candidate_fixture(
        tmp_path
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        identity = build_candidate_runtime_identity(candidate)

        other_path = tmp_path / "runtime" / "same-bytes.json"
        other_path.write_bytes(config_bytes)
        with pytest.raises(ValueError, match="does not match exact live config path"):
            identity.verify_against_live_config(other_path)

        live_config.write_bytes(config_bytes + b" ")
        with pytest.raises(ValueError, match="byte length drift"):
            identity.verify_against_live_config(live_config)

        same_length_drift = bytes([config_bytes[0] ^ 1]) + config_bytes[1:]
        live_config.write_bytes(same_length_drift)
        with pytest.raises(ValueError, match="Config bytes drift"):
            identity.verify_against_live_config(live_config)

        live_config.write_bytes(config_bytes)
        bad_sha_identity = replace(identity, config_sha256="0" * 64)
        with pytest.raises(ValueError, match="Config SHA256 drift"):
            bad_sha_identity.verify_against_live_config(live_config)

        replacement = tmp_path / "runtime" / "replacement.json"
        replacement.write_bytes(config_bytes)
        os.replace(replacement, live_config)
        with pytest.raises(ValueError, match="Config file identity drift"):
            identity.verify_against_live_config(live_config)
