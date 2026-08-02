from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import Mock, patch

from promote_supervisor_runtime import (
    CandidateRuntimeIdentity,
    FilesystemIdentity,
    PathComponentIdentity,
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


def _verified_identity_dependency(repo: Path) -> Mock:
    identity = Mock(spec=CandidateRuntimeIdentity)
    identity.candidate_root = repo
    identity.candidate_root_device = 1
    identity.candidate_root_inode = 2
    identity.git_directory_device = 3
    identity.git_directory_inode = 4
    identity.git_objects_device = 5
    identity.git_objects_inode = 6
    identity.git_config_device = 7
    identity.git_config_inode = 8
    identity.git_head_device = 9
    identity.git_head_inode = 10
    identity.git_index_device = 11
    identity.git_index_inode = 12
    identity.basename = "a" * 40
    identity.head_commit = "a" * 40
    identity.tracked_tree_identity = "b" * 40
    identity.accepted_dev_commit = "c" * 40
    identity.remote_url = "https://github.com/ajoe734/pantheon.git"
    identity.canonical_remote = "github.com/ajoe734/pantheon"
    identity.repository_slug = "ajoe734/pantheon"
    identity.config_path = promotion.LIVE_SUPERVISOR_CONFIG_PATH
    identity.config_device = 13
    identity.config_inode = 14
    identity.config_path_components = (
        PathComponentIdentity(
            path=promotion.LIVE_SUPERVISOR_CONFIG_PATH,
            identity=FilesystemIdentity(device=13, inode=14, mode=0),
        ),
    )
    identity.config_byte_length = 2
    identity.config_sha256 = "d" * 64
    return identity


@patch("promote_supervisor_runtime.lock_held", return_value=True)
@patch("promote_supervisor_runtime.pid_is_alive", return_value=True)
@patch("supervisor_runtime_health.lock_held", return_value=True)
@patch("supervisor_runtime_health.pid_matches_supervisor", return_value=True)
def test_promotion_snapshot_eligible_when_healthy(mock_matches, mock_sup_lock, mock_alive, mock_lock, tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    create_realistic_healthy_fixture(repo)
    identity = _verified_identity_dependency(repo)

    with patch(
        "promote_supervisor_runtime.build_candidate_runtime_identity",
        return_value=identity,
    ) as identity_builder:
        snapshot = capture_promotion_snapshot(repo, now=now)

    assert snapshot["eligible_for_promotion"] is True
    assert len(snapshot["file_errors"]) == 0
    assert all(inv["ok"] for inv in snapshot["invariants"])
    identity_builder.assert_called_once_with(repo)
    identity.verify_immutable_snapshot.assert_called_once_with()


@patch("promote_supervisor_runtime.lock_held", return_value=True)
@patch("promote_supervisor_runtime.pid_is_alive", return_value=True)
@patch("supervisor_runtime_health.lock_held", return_value=True)
@patch("supervisor_runtime_health.pid_matches_supervisor", return_value=True)
def test_promotion_snapshot_fails_closed_when_identity_capture_is_missing(
    mock_matches,
    mock_sup_lock,
    mock_alive,
    mock_lock,
    tmp_path: Path,
) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    create_realistic_healthy_fixture(repo)

    with patch(
        "promote_supervisor_runtime.build_candidate_runtime_identity",
        side_effect=ValueError("identity unavailable"),
    ):
        snapshot = capture_promotion_snapshot(repo, now=now)

    identity_invariant = next(
        item
        for item in snapshot["invariants"]
        if item["name"] == "candidate_runtime_identity_immutable"
    )
    assert snapshot["eligible_for_promotion"] is False
    assert identity_invariant["ok"] is False
    assert identity_invariant["details"]["error"] == "identity unavailable"


def test_capture_promotion_snapshot_fail_closed_on_missing_files(tmp_path: Path) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    snapshot = capture_promotion_snapshot(repo, now=now)

    assert snapshot["eligible_for_promotion"] is False
    assert len(snapshot["file_errors"]) > 0
    inv = next(i for i in snapshot["invariants"] if i["name"] == "config_and_state_files_readable")
    assert inv["ok"] is False


def test_main_preserves_lexical_candidate_alias_for_identity_rejection(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "command-runtimes" / ("a" * 40)
    candidate.mkdir(parents=True)
    alias = tmp_path / "candidate-alias"
    alias.symlink_to(candidate, target_is_directory=True)
    snapshot = {
        "eligible_for_promotion": False,
        "timestamp": "2026-08-01T00:00:00Z",
        "invariants": [],
    }

    with patch.object(
        sys,
        "argv",
        ["promote_supervisor_runtime.py", "--repo", str(alias)],
    ), patch(
        "promote_supervisor_runtime.capture_promotion_snapshot",
        return_value=snapshot,
    ) as capture:
        assert promotion.main() == 1

    capture.assert_called_once_with(alias, config_path_arg=None)


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
    *,
    gitlink_path: str = "lean",
) -> tuple[Path, Path, Path, str, str, bytes]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--initial-branch=dev")
    _git(source, "config", "user.name", "Promotion Test")
    _git(source, "config", "user.email", "promotion@example.test")
    (source / "README.md").write_text("trusted candidate\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "trusted candidate")
    gitlink_commit = _git(source, "rev-parse", "HEAD")
    _git(
        source,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{gitlink_commit},{gitlink_path}",
    )
    _git(source, "commit", "-m", "track lean-shaped gitlink")
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
        assert identity.git_directory_inode == (candidate / ".git").stat().st_ino
        assert identity.git_objects_inode == (candidate / ".git" / "objects").stat().st_ino
        assert identity.git_config_inode == (candidate / ".git" / "config").stat().st_ino
        assert identity.git_head_inode == (candidate / ".git" / "HEAD").stat().st_ino
        assert identity.git_index_inode == (candidate / ".git" / "index").stat().st_ino
        assert identity.basename == commit
        assert identity.head_commit == commit
        assert identity.tracked_tree_identity == tree
        assert identity.accepted_dev_commit == commit
        assert identity.canonical_remote == "github.com/ajoe734/pantheon"
        assert identity.repository_slug == "ajoe734/pantheon"
        assert identity.config_path == live_config
        assert identity.config_path_components[-1].path == live_config
        assert identity.config_path_components[-2].path == live_config.parent
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


def test_candidate_root_rejects_missing_direct_child(tmp_path: Path) -> None:
    candidate, parent, remote, commit, _tree, _config_bytes = _make_candidate_fixture(
        tmp_path
    )
    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(FileNotFoundError):
            resolve_candidate_root(parent / ("f" * 40))


def test_candidate_root_rejects_nested_child(tmp_path: Path) -> None:
    _candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    nested = parent / "nested" / ("e" * 40)
    nested.mkdir(parents=True)
    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="not a direct child"):
            resolve_candidate_root(nested)


def test_candidate_root_rejects_parent_traversal(tmp_path: Path) -> None:
    _candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="not a direct child"):
            resolve_candidate_root(parent / ".." / ("d" * 40))


def test_candidate_root_rejects_basename_head_mismatch(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        wrong_basename = parent / ("b" * 40)
        candidate.rename(wrong_basename)
        with pytest.raises(ValueError, match="does not match HEAD commit"):
            build_candidate_runtime_identity(wrong_basename)


def test_candidate_root_rejects_leaf_symlink(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        symlink_candidate = parent / ("c" * 40)
        symlink_candidate.symlink_to(candidate, target_is_directory=True)
        with pytest.raises(ValueError, match="symlink|wrong file type"):
            resolve_candidate_root(symlink_candidate)


def test_candidate_root_rejects_symlinked_trusted_parent(tmp_path: Path) -> None:
    _candidate, parent, _remote, commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    parent_alias = tmp_path / "command-runtimes-alias"
    parent_alias.symlink_to(parent, target_is_directory=True)
    with patch(
        "promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX",
        parent_alias,
    ):
        with pytest.raises(ValueError, match="symlink|non-directory component"):
            resolve_candidate_root(parent_alias / commit)


def test_git_identity_rejects_external_symlinked_git_directory(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    external_git = tmp_path / "external-git"
    (candidate / ".git").rename(external_git)
    (candidate / ".git").symlink_to(external_git, target_is_directory=True)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Candidate Git directory.*symlink"):
            resolve_candidate_root(candidate)


def test_git_identity_rejects_linked_worktree_gitfile(tmp_path: Path) -> None:
    candidate, parent, _remote, commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    shutil.rmtree(candidate)
    _git(
        tmp_path / "source",
        "worktree",
        "add",
        "--detach",
        str(candidate),
        commit,
    )
    assert (candidate / ".git").is_file()

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Candidate Git directory.*wrong type"):
            resolve_candidate_root(candidate)


def test_git_identity_rejects_external_commondir_pointer(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    (candidate / ".git" / "commondir").write_text(
        str(tmp_path / "external-common-dir"),
        encoding="utf-8",
    )

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Forbidden Candidate Git commondir"):
            resolve_candidate_root(candidate)


def test_git_identity_rejects_external_object_alternates(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    external_objects = tmp_path / "external-objects"
    external_objects.mkdir()
    alternates = candidate / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(f"{external_objects}\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="objects/info/alternates"):
            resolve_candidate_root(candidate)


def test_git_identity_rejects_symlinked_object_directory(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    objects = candidate / ".git" / "objects"
    external_objects = tmp_path / "external-objects"
    objects.rename(external_objects)
    objects.symlink_to(external_objects, target_is_directory=True)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Git objects directory.*symlink"):
            resolve_candidate_root(candidate)


def test_git_identity_rejects_external_loose_object_fanout_symlink(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    objects = candidate / ".git" / "objects"
    loose_fanout = next(
        entry
        for entry in objects.iterdir()
        if entry.is_dir() and len(entry.name) == 2
    )
    external_fanout = tmp_path / "external-loose-objects"
    loose_fanout.rename(external_fanout)
    loose_fanout.symlink_to(external_fanout, target_is_directory=True)
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent,
        remote,
        live_config,
    )

    with parent_patch, remote_patch, config_patch:
        with pytest.raises(ValueError, match="objects directory/.+ cannot be a symlink"):
            build_candidate_runtime_identity(candidate)


def test_git_identity_rejects_external_symlinked_index(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    index = candidate / ".git" / "index"
    external_index = tmp_path / "external-index"
    index.rename(external_index)
    index.symlink_to(external_index)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Candidate Git index.*symlink"):
            resolve_candidate_root(candidate)


def test_git_identity_rejects_symlinked_ref_metadata(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    branch_ref = candidate / ".git" / "refs" / "heads" / "dev"
    external_ref = tmp_path / "external-ref"
    branch_ref.rename(external_ref)
    branch_ref.symlink_to(external_ref)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="refs directory/heads/dev.*symlink"):
            resolve_candidate_root(candidate)


def test_git_identity_rejects_hardlinked_config_metadata(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    git_config = candidate / ".git" / "config"
    external_config = tmp_path / "external-config-hardlink"
    git_config.rename(external_config)
    os.link(external_config, git_config)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Git config must not be hard-linked"):
            resolve_candidate_root(candidate)


def test_git_identity_rejects_external_config_include(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    external_config = tmp_path / "external-git-config"
    external_config.write_text("[core]\n\tbare = true\n", encoding="utf-8")
    with (candidate / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write(f"[include]\n\tpath = {external_config}\n")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="cannot include external config"):
            resolve_candidate_root(candidate)


def test_candidate_root_rejects_tmp_prefix(tmp_path: Path) -> None:
    trusted_parent = tmp_path / "command-runtimes"
    trusted_parent.mkdir()
    tmp_candidate = tmp_path / ("a" * 40)
    tmp_candidate.mkdir()
    with patch(
        "promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX",
        trusted_parent,
    ):
        with pytest.raises(ValueError, match="not a direct child"):
            resolve_candidate_root(tmp_candidate)


def test_candidate_root_rejects_worker_worktree_prefix(tmp_path: Path) -> None:
    trusted_parent = tmp_path / "command-runtimes"
    trusted_parent.mkdir()
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
            resolve_candidate_root(worker_candidate)


def test_candidate_root_deleted_during_capture_is_rejected(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = _make_candidate_fixture(
        tmp_path
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    original_capture = promotion._capture_git_identity

    def remove_after_git_capture(
        root: Path | promotion.CandidateRootHandle,
        basename: str,
    ):
        result = original_capture(root, basename)
        root_path = root.path if isinstance(root, promotion.CandidateRootHandle) else root
        shutil.rmtree(root_path)
        return result

    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch, patch(
        "promote_supervisor_runtime._capture_git_identity",
        side_effect=remove_after_git_capture,
    ):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            build_candidate_runtime_identity(candidate)


def test_candidate_root_parent_swap_during_capture_is_rejected(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    attacker_parent = tmp_path / "attacker-command-runtimes"
    attacker_parent.mkdir()
    shutil.copytree(candidate, attacker_parent / commit)
    original_parent = tmp_path / "original-command-runtimes"
    original_capture = promotion._capture_git_identity
    swapped = False

    def swap_parent_then_capture(
        root: Path | promotion.CandidateRootHandle,
        basename: str,
    ):
        nonlocal swapped
        if not swapped:
            parent.rename(original_parent)
            parent.symlink_to(attacker_parent, target_is_directory=True)
            swapped = True
        return original_capture(root, basename)

    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch, patch(
        "promote_supervisor_runtime._capture_git_identity",
        side_effect=swap_parent_then_capture,
    ):
        with pytest.raises(ValueError, match="symlink|non-directory component"):
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


def test_trusted_dev_fetch_rejects_git_environment_url_rewrite(
    tmp_path: Path,
) -> None:
    _candidate, _parent, trusted_remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    attacker_source = tmp_path / "attacker-source"
    attacker_source.mkdir()
    _git(attacker_source, "init", "--initial-branch=dev")
    _git(attacker_source, "config", "user.name", "Attacker")
    _git(attacker_source, "config", "user.email", "attacker@example.test")
    (attacker_source / "README.md").write_text(
        "attacker dev\n",
        encoding="utf-8",
    )
    _git(attacker_source, "add", "README.md")
    _git(attacker_source, "commit", "-m", "attacker dev")
    attacker_commit = _git(attacker_source, "rev-parse", "HEAD")
    attacker_remote = tmp_path / "attacker-remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(attacker_source), str(attacker_remote)],
        capture_output=True,
        check=True,
        text=True,
    )

    injected_environment = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{attacker_remote.as_uri()}.insteadOf",
        "GIT_CONFIG_VALUE_0": trusted_remote.as_uri(),
        "GIT_ALLOW_PROTOCOL": "file",
    }
    with patch(
        "promote_supervisor_runtime.TRUSTED_ORIGIN_DEV_URL",
        trusted_remote.as_uri(),
    ), patch.dict(os.environ, injected_environment, clear=False):
        with pytest.raises(ValueError, match="cat-file"):
            promotion._fetch_trusted_dev_identity(attacker_commit)
        sanitized = promotion._subprocess_environment()

    assert not any(name.startswith("GIT_") for name in sanitized if name not in {
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
    })


def test_git_identity_revalidation_rejects_metadata_inode_replacement(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent,
        remote,
        live_config,
    )
    with parent_patch, remote_patch, config_patch:
        identity = build_candidate_runtime_identity(candidate)
        git_config = candidate / ".git" / "config"
        replacement = candidate / ".git" / "config.replacement"
        replacement.write_bytes(git_config.read_bytes())
        os.replace(replacement, git_config)
        with pytest.raises(ValueError, match="Git metadata identity drift"):
            identity.verify_immutable_snapshot()


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


def test_git_identity_rejects_skip_worktree_hidden_drift(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    _git(candidate, "update-index", "--skip-worktree", "README.md")
    (candidate / "README.md").write_text("hidden skip-worktree drift\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Forbidden tracked index flag"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_assume_unchanged_hidden_drift(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    _git(candidate, "update-index", "--assume-unchanged", "README.md")
    (candidate / "README.md").write_text(
        "hidden assume-unchanged drift\n",
        encoding="utf-8",
    )

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Forbidden tracked index flag"):
            verify_working_tree_cleanliness(candidate)


@pytest.mark.parametrize(
    "relative_path",
    [
        *(str(path) for path in sorted(promotion.ALLOWED_GENERATED_UNTRACKED_FILES)),
        ".orchestrator/task-briefs/sup_runtime_identity_001.md",
    ],
)
def test_git_identity_allows_each_enumerated_generated_untracked_path(
    tmp_path: Path,
    relative_path: str,
) -> None:
    candidate, parent, _remote, _commit, tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    generated = candidate / relative_path
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text(
        "generated task context\n",
        encoding="utf-8",
    )
    if relative_path != ".orchestrator/task-briefs/sup_runtime_identity_001.md":
        exclude = candidate / ".git" / "info" / "exclude"
        exclude.write_text(f"/{relative_path}\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        assert verify_working_tree_cleanliness(candidate) == tree


@pytest.mark.parametrize(
    "relative_path",
    [
        ".orchestrator/supervisor.lock",
        ".orchestrator/task-briefs/sup_runtime_identity_001.md",
    ],
)
def test_git_identity_rejects_allowlisted_generated_symlink(
    tmp_path: Path,
    relative_path: str,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    external_file = tmp_path / "external-generated-file"
    external_file.write_text("external\n", encoding="utf-8")
    generated = candidate / relative_path
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.symlink_to(external_file)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="symlink|wrong type"):
            verify_working_tree_cleanliness(candidate)


@pytest.mark.parametrize(
    "relative_path",
    [
        ".orchestrator/supervisor.lock",
        ".orchestrator/task-briefs/evil.md",
    ],
)
def test_git_identity_rejects_ignored_directory_at_allowlisted_file_path(
    tmp_path: Path,
    relative_path: str,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    generated_directory = candidate / relative_path
    generated_directory.mkdir(parents=True, exist_ok=True)
    (generated_directory / "payload.py").write_text(
        "raise RuntimeError('injected')\n",
        encoding="utf-8",
    )
    exclude = candidate / ".git" / "info" / "exclude"
    exclude.write_text(f"/{relative_path}/\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Forbidden ignored directory"):
            verify_working_tree_cleanliness(candidate)


@pytest.mark.parametrize(
    ("relative_path", "ignored"),
    [
        (".orchestrator/config.json", False),
        (".orchestrator/replacement.py", False),
        (".orchestrator/task-briefs/replacement.py", False),
        (".orchestrator/task-briefs/nested/replacement.md", False),
        (".orchestrator/task-briefs/UPPERCASE.md", False),
        ("scripts/injected.py", False),
        ("scripts/ignored-injected.py", True),
    ],
)
def test_git_identity_rejects_each_forbidden_untracked_or_ignored_path(
    tmp_path: Path,
    relative_path: str,
    ignored: bool,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    forbidden = candidate / relative_path
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("forbidden\n", encoding="utf-8")
    if ignored:
        exclude = candidate / ".git" / "info" / "exclude"
        exclude.write_text(f"/{relative_path}\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        message = "Forbidden ignored file" if ignored else "Forbidden untracked file"
        with pytest.raises(ValueError, match=message):
            verify_working_tree_cleanliness(candidate)


def test_live_config_rejects_external_path(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, config_bytes = _make_candidate_fixture(
        tmp_path
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    external_config = tmp_path / "runtime" / "other-config.json"
    external_config.write_bytes(config_bytes)
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        with pytest.raises(ValueError, match="does not match exact live config path"):
            build_candidate_runtime_identity(candidate, config_path=external_config)


def test_live_config_rejects_exact_leaf_symlink(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    real_config = tmp_path / "runtime" / "real-config.json"
    live_config.rename(real_config)
    live_config.symlink_to(real_config)

    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        with pytest.raises(ValueError, match="symlink|wrong file type"):
            build_candidate_runtime_identity(candidate)


def test_live_config_rejects_symlinked_parent_component(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"

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
        with pytest.raises(ValueError, match="symlink|non-directory component"):
            build_candidate_runtime_identity(candidate)


def test_live_config_parent_swap_during_open_is_rejected(tmp_path: Path) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    original_runtime = tmp_path / "original-runtime"
    attacker_runtime = tmp_path / "attacker-runtime"
    attacker_runtime.mkdir()
    (attacker_runtime / live_config.name).write_bytes(b'{"attacker":true}\n')
    original_open_directory = promotion._open_directory_descriptor
    swapped = False

    def swap_parent_after_open(path: Path, *, label: str) -> int:
        nonlocal swapped
        descriptor = original_open_directory(path, label=label)
        if path == live_config.parent and not swapped:
            live_config.parent.rename(original_runtime)
            live_config.parent.symlink_to(attacker_runtime, target_is_directory=True)
            swapped = True
        return descriptor

    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch, patch(
        "promote_supervisor_runtime._open_directory_descriptor",
        side_effect=swap_parent_after_open,
    ):
        with pytest.raises(ValueError, match="symlink|non-directory component"):
            build_candidate_runtime_identity(candidate)


def test_live_config_same_inode_parent_replacement_during_capture_is_rejected(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    original_runtime = tmp_path / "original-runtime"
    original_open_directory = promotion._open_directory_descriptor
    swapped = False

    def replace_parent_after_open(path: Path, *, label: str) -> int:
        nonlocal swapped
        descriptor = original_open_directory(path, label=label)
        if path == live_config.parent and not swapped:
            live_config.parent.rename(original_runtime)
            live_config.parent.mkdir()
            os.link(original_runtime / live_config.name, live_config)
            swapped = True
        return descriptor

    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent,
        remote,
        live_config,
    )
    with parent_patch, remote_patch, config_patch, patch(
        "promote_supervisor_runtime._open_directory_descriptor",
        side_effect=replace_parent_after_open,
    ):
        with pytest.raises(ValueError, match="component changed"):
            build_candidate_runtime_identity(candidate)


def _build_test_candidate_identity(
    tmp_path: Path,
) -> tuple[CandidateRuntimeIdentity, Path, bytes]:
    candidate, parent, remote, _commit, _tree, config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        identity = build_candidate_runtime_identity(candidate)
    return identity, live_config, config_bytes


def test_live_config_revalidation_rejects_path_drift(tmp_path: Path) -> None:
    identity, live_config, config_bytes = _build_test_candidate_identity(tmp_path)
    other_path = tmp_path / "runtime" / "same-bytes.json"
    other_path.write_bytes(config_bytes)

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(ValueError, match="does not match exact live config path"):
            identity.verify_against_live_config(other_path)


def test_live_config_revalidation_rejects_captured_path_tampering(
    tmp_path: Path,
) -> None:
    identity, live_config, config_bytes = _build_test_candidate_identity(tmp_path)
    other_path = tmp_path / "runtime" / "same-bytes.json"
    other_path.write_bytes(config_bytes)
    tampered = replace(identity, config_path=other_path)

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(ValueError, match="not the exact live supervisor config"):
            tampered.verify_against_live_config(other_path)


def test_live_config_revalidation_rejects_byte_length_drift(tmp_path: Path) -> None:
    identity, live_config, config_bytes = _build_test_candidate_identity(tmp_path)
    live_config.write_bytes(config_bytes + b" ")

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(ValueError, match="byte length drift"):
            identity.verify_against_live_config(live_config)


def test_live_config_revalidation_rejects_same_length_byte_drift(
    tmp_path: Path,
) -> None:
    identity, live_config, config_bytes = _build_test_candidate_identity(tmp_path)
    same_length_drift = bytes([config_bytes[0] ^ 1]) + config_bytes[1:]
    live_config.write_bytes(same_length_drift)

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(ValueError, match="Config bytes drift"):
            identity.verify_against_live_config(live_config)


def test_live_config_revalidation_rejects_sha_drift(tmp_path: Path) -> None:
    identity, live_config, _config_bytes = _build_test_candidate_identity(tmp_path)
    bad_sha_identity = replace(identity, config_sha256="0" * 64)

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(ValueError, match="Config SHA256 drift"):
            bad_sha_identity.verify_against_live_config(live_config)


def test_live_config_revalidation_rejects_inode_replacement(tmp_path: Path) -> None:
    identity, live_config, config_bytes = _build_test_candidate_identity(tmp_path)
    replacement = tmp_path / "runtime" / "replacement.json"
    replacement.write_bytes(config_bytes)
    os.replace(replacement, live_config)

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(ValueError, match="Config file identity drift"):
            identity.verify_against_live_config(live_config)


def test_live_config_revalidation_rejects_same_inode_parent_replacement(
    tmp_path: Path,
) -> None:
    identity, live_config, _config_bytes = _build_test_candidate_identity(tmp_path)
    original_runtime = tmp_path / "original-runtime"
    live_config.parent.rename(original_runtime)
    live_config.parent.mkdir()
    os.link(original_runtime / live_config.name, live_config)

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(ValueError, match="path component identity drift"):
            identity.verify_against_live_config(live_config)
