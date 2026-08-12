from __future__ import annotations

import json
import errno
import fcntl
import hashlib
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from unittest.mock import Mock, patch

from promote_supervisor_runtime import (
    CandidateRuntimeIdentity,
    ExpectedSupervisorProcessContract,
    FilesystemIdentity,
    GovernedSupervisorLaunchContract,
    PromotionPlan,
    PromotionLock,
    PromotionState,
    PromotionTransaction,
    ProcessCwdIdentity,
    ProcessGeneration,
    ProcessLaunchError,
    ProcfsRuntimeProcessReader,
    RuntimeAdmissionLock,
    RuntimeObservation,
    SupervisorAdmissionLockIdentity,
    SupervisorConfigVariant,
    SupervisorProcessIdentity,
    build_candidate_runtime_identity,
    build_governed_supervisor_launch_contract,
    build_scrubbed_launch_environment,
    capture_promotion_snapshot,
    discover_incumbent_supervisor_process,
    evaluate_promotion_invariants,
    parse_origin_url,
    resolve_candidate_root,
    validate_remote_url,
    verify_git_head_and_dev_ancestry,
    verify_working_tree_cleanliness,
)
import promote_supervisor_runtime as promotion


@pytest.fixture(autouse=True)
def _authority_public_key_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
        '{"test-bridge":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}',
    )
    monkeypatch.setenv(
        "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON",
        '{"test-operator":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}',
    )


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("arguments", [(), ("--promote",)])
def test_shell_entrypoint_disables_candidate_bytecode(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    scripts_dir = tmp_path / "candidate" / "scripts"
    scripts_dir.mkdir(parents=True)
    wrapper = scripts_dir / "promote-supervisor-runtime.sh"
    shutil.copy2(Path(__file__).with_name("promote-supervisor-runtime.sh"), wrapper)
    wrapper.chmod(0o755)
    (scripts_dir / "promote_supervisor_runtime.py").write_text(
        "import sys\nprint(sys.dont_write_bytecode)\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("PYTHONDONTWRITEBYTECODE", None)

    result = subprocess.run(
        [str(wrapper), *arguments],
        cwd=scripts_dir.parent,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
    assert not (scripts_dir / "__pycache__").exists()


def test_removed_mutable_bootstrap_cli_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["promote_supervisor_runtime.py", "--bootstrap-mutable-incumbent"],
    )

    with pytest.raises(SystemExit) as raised:
        promotion.parse_args()

    assert raised.value.code == 2


def test_prepare_rejects_nonimmutable_incumbent_without_fallback(
    tmp_path: Path,
) -> None:
    candidate = Mock(spec=CandidateRuntimeIdentity)
    candidate.candidate_root = tmp_path / ("b" * 40)
    generation = ProcessGeneration(pid=101, starttime_ticks=202, state="S")
    cwd = ProcessCwdIdentity(
        path=tmp_path / ("a" * 40),
        device=1,
        inode=2,
    )
    backend = promotion.OSPromotionBackend(reader=Mock())

    with patch(
        "promote_supervisor_runtime.build_candidate_runtime_identity",
        side_effect=[candidate, ValueError("incumbent root is dirty")],
    ), patch(
        "promote_supervisor_runtime._discover_supervisor_seed",
        return_value=(generation, ("python", "supervisor.py"), cwd),
    ), pytest.raises(ValueError, match="incumbent root is dirty"):
        backend.prepare(candidate.candidate_root)


def create_realistic_healthy_fixture(repo: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    status_root = repo.parent / f"{repo.name}-status-root"
    state_path = status_root / ".orchestrator" / "state.json"
    status_path = status_root / "ai-status.json"
    capabilities_path = status_root / ".orchestrator" / "provider_capabilities.json"
    config = {
        "paths": {
            "state_file": str(state_path),
            "status_file": str(status_path),
            "provider_capabilities": str(capabilities_path),
            "event_queue": str(status_root / ".orchestrator" / "event-queue.jsonl"),
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
            "last_cycle_metrics": {
                "cycle_elapsed_seconds": 1.0,
                "queue_to_start": {"max_seconds": 0.0},
            },
            "task_state_shadow": {
                "mode": "authoritative",
                "ok": True,
                "caught_up": True,
                "last_error": None,
                "projected_state_sha256": "abc123sha",
                "expected_state_sha256": "abc123sha",
            },
        },
        "workers": {},
        "queue": {"events": {}},
        "worker_worktrees": {"leases": {}},
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
    write_json(state_path, state)
    (status_root / ".orchestrator" / "supervisor.pid").write_text(
        "12345\n", encoding="utf-8"
    )
    write_json(status_path, ai_status)
    write_json(capabilities_path, provider_capabilities)
    (status_root / ".orchestrator" / "event-queue.jsonl").write_text(
        "", encoding="utf-8"
    )

    return config, state, ai_status, provider_capabilities


def _verified_identity_dependency(repo: Path) -> Mock:
    status_root = repo.parent / f"{repo.name}-status-root"
    runtime_root = repo.parent / f"{repo.name}-runtime"
    worker_worktree_root = repo.parent / f"{repo.name}-worker-worktrees"
    live_config_path = runtime_root / "live-supervisor-mainroot-config.json"
    event_log = runtime_root / "task-state-events.jsonl"
    (status_root / ".orchestrator" / "logs").mkdir(parents=True, exist_ok=True)
    worker_worktree_root.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    event_log.write_text("", encoding="utf-8")
    executable = Path(sys.executable).resolve()
    argv = (
        str(executable),
        "-u",
        "-B",
        str(repo / ".orchestrator" / "supervisor.py"),
        "--config",
        str(live_config_path),
        "--verbose",
    )
    source_specs = [
        (repo / ".orchestrator" / "supervisor.py", False),
        (repo / ".orchestrator" / "supervisor_watchdog.py", True),
        (repo / "scripts" / "run-supervisor-watchdog.sh", True),
        (repo / "scripts" / "sync-dev-root.sh", True),
        (repo / "scripts" / "ai-status.sh", True),
        (repo / "scripts" / "ai_status.py", False),
        (repo / "scripts" / "provision_live_supervisor_config.py", False),
    ]
    for path, executable_source in source_specs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture: {path.name}\n", encoding="utf-8")
        if executable_source:
            path.chmod(0o755)
    live_config = {
        "watchdog": {"supervisor_command": list(argv)},
        "paths": {
            "status_file": str(status_root / "ai-status.json"),
            "state_file": str(status_root / ".orchestrator" / "state.json"),
            "provider_capabilities": str(status_root / ".orchestrator" / "provider_capabilities.json"),
        },
        "task_state_store": {
            "mode": "authoritative",
            "event_log": str(event_log),
        },
        "worker_worktrees": {"root": str(worker_worktree_root)},
    }
    config_bytes = (json.dumps(live_config, sort_keys=True) + "\n").encode("utf-8")
    live_config_path.write_bytes(config_bytes)
    root_stat = repo.stat()
    config_stat = live_config_path.stat()
    identity = Mock(spec=CandidateRuntimeIdentity)
    identity.candidate_root = repo
    identity.candidate_root_device = root_stat.st_dev
    identity.candidate_root_inode = root_stat.st_ino
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
    identity.config_path = live_config_path
    identity.config_device = config_stat.st_dev
    identity.config_inode = config_stat.st_ino
    identity.config_path_components = ()
    identity.config_bytes = config_bytes
    identity.config_byte_length = len(config_bytes)
    identity.config_sha256 = promotion.hashlib.sha256(config_bytes).hexdigest()
    return identity


def _verified_process_identity_dependency(repo: Path) -> SupervisorProcessIdentity:
    status_root = repo.parent / f"{repo.name}-status-root"
    live_config_path = repo.parent / f"{repo.name}-runtime" / "live-supervisor-mainroot-config.json"
    generation = ProcessGeneration(pid=12345, starttime_ticks=67890, state="S")
    lock = SupervisorAdmissionLockIdentity(
        path=status_root / ".orchestrator" / "supervisor.lock",
        device=20,
        inode=21,
        byte_length=6,
        sha256="e" * 64,
        mtime_ns=22,
        ctime_ns=23,
        kernel_lock_id="42",
        kernel_lock_kind="FLOCK",
        kernel_lock_class="ADVISORY",
        kernel_lock_mode="WRITE",
        kernel_lock_start="0",
        kernel_lock_end="EOF",
        owner_pid=generation.pid,
        owner_starttime_ticks=generation.starttime_ticks,
    )
    executable = Path(sys.executable).resolve()
    argv = (
        str(executable),
        "-u",
        str(repo / ".orchestrator" / "supervisor.py"),
        "--config",
        str(live_config_path),
        "--verbose",
    )
    repo_stat = repo.stat()
    return SupervisorProcessIdentity(
        generation=generation,
        executable=executable,
        argv=argv,
        entrypoint=repo / ".orchestrator" / "supervisor.py",
        config_path=live_config_path,
        cwd=ProcessCwdIdentity(
            path=repo,
            device=repo_stat.st_dev,
            inode=repo_stat.st_ino,
        ),
        cwd_commit="a" * 40,
        cwd_tree="b" * 40,
        environment_contract=(
            ("PANTHEON_COMMAND_ROOT", str(repo)),
            ("PANTHEON_COMMAND_RUNTIME_SHA", "a" * 40),
            ("PANTHEON_STATUS_ROOT", str(status_root)),
            ("PYTHONDONTWRITEBYTECODE", "1"),
        ),
        admission_lock=lock,
    )


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


@patch("promote_supervisor_runtime.lock_held", return_value=True)
@patch("promote_supervisor_runtime.pid_is_alive", return_value=True)
@patch("supervisor_runtime_health.lock_held", return_value=True)
@patch("supervisor_runtime_health.pid_matches_supervisor", return_value=True)
def test_promotion_snapshot_requires_exact_process_identity(
    mock_matches,
    mock_sup_lock,
    mock_alive,
    mock_lock,
    tmp_path: Path,
) -> None:
    repo = tmp_path
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    create_realistic_healthy_fixture(repo)
    identity = _verified_identity_dependency(repo)

    with patch(
        "promote_supervisor_runtime.build_candidate_runtime_identity",
        return_value=identity,
    ), patch(
        "promote_supervisor_runtime.discover_incumbent_supervisor_process",
        side_effect=ValueError("zero exact incumbents"),
    ):
        snapshot = capture_promotion_snapshot(repo, now=now)

    process_invariant = next(
        item
        for item in snapshot["invariants"]
        if item["name"]
        == "incumbent_supervisor_process_identity_immutable"
    )
    assert snapshot["eligible_for_promotion"] is False
    assert process_invariant["ok"] is False
    assert process_invariant["details"]["error"] == "zero exact incumbents"


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


@patch("promote_supervisor_runtime.lock_held", return_value=True)
@patch("promote_supervisor_runtime.pid_is_alive", return_value=True)
def test_evaluate_promotion_invariants_accepts_verified_v1_headless_baseline(
    _mock_alive: Mock,
    _mock_lock: Mock,
    tmp_path: Path,
) -> None:
    repo = tmp_path
    config, state, ai_status, provider_capabilities = create_realistic_healthy_fixture(repo)
    v1_log = repo / "runtime" / "task-state-events.jsonl"
    v1_log.parent.mkdir(parents=True, exist_ok=True)
    v1_log.write_text(
        json.dumps(
            {
                "version": 1,
                "type": "task_state_committed",
                "sequence": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config["task_state_store"] = {"event_log": str(v1_log)}
    health_report = {
        "healthy": False,
        "supervisor": state["supervisor"],
        "checks": [
            {
                "name": "readiness_task_head_accessible",
                "ok": False,
                "task_head": str(v1_log.with_name(f"{v1_log.name}.head.json")),
                "error": "FileNotFoundError: V1 has no V2 task head",
            }
        ],
    }

    invariants = evaluate_promotion_invariants(
        health_report=health_report,
        ai_status=ai_status,
        state=state,
        provider_capabilities=provider_capabilities,
        lock_path=Path("/tmp/fake.lock"),
        now=datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc),
        config=config,
        allow_v1_headless_task_store=True,
    )

    runtime_health = next(
        invariant for invariant in invariants if invariant["name"] == "runtime_health_clean"
    )
    assert runtime_health["ok"] is True
    assert runtime_health["details"]["v1_headless_baseline_compatibility"] is True


@patch("promote_supervisor_runtime.lock_held", return_value=True)
@patch("promote_supervisor_runtime.pid_is_alive", return_value=True)
def test_evaluate_promotion_invariants_rejects_headless_non_v1_store(
    _mock_alive: Mock,
    _mock_lock: Mock,
    tmp_path: Path,
) -> None:
    repo = tmp_path
    config, state, ai_status, provider_capabilities = create_realistic_healthy_fixture(repo)
    event_log = repo / "runtime" / "task-state-events.jsonl"
    event_log.parent.mkdir(parents=True, exist_ok=True)
    event_log.write_text(
        json.dumps({"version": 2, "type": "task_state_v2_genesis", "sequence": 1})
        + "\n",
        encoding="utf-8",
    )
    config["task_state_store"] = {"event_log": str(event_log)}
    health_report = {
        "healthy": False,
        "supervisor": state["supervisor"],
        "checks": [
            {
                "name": "readiness_task_head_accessible",
                "ok": False,
                "task_head": str(event_log.with_name(f"{event_log.name}.head.json")),
                "error": "FileNotFoundError: V2 task head missing",
            }
        ],
    }

    invariants = evaluate_promotion_invariants(
        health_report=health_report,
        ai_status=ai_status,
        state=state,
        provider_capabilities=provider_capabilities,
        lock_path=Path("/tmp/fake.lock"),
        now=datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc),
        config=config,
        allow_v1_headless_task_store=True,
    )

    runtime_health = next(
        invariant for invariant in invariants if invariant["name"] == "runtime_health_clean"
    )
    assert runtime_health["ok"] is False
    assert runtime_health["details"]["v1_headless_baseline_compatibility"] is False


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


def test_v1_execution_authority_must_drain_before_task_store_cutover() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    active_state = {
        "workers": {"run-v1": {"status": "running", "task_id": "TASK-V1"}},
        "worker_worktrees": {
            "leases": {"TASK-V1": {"status": "active", "task_id": "TASK-V1"}}
        },
        "queue": {"events": {}},
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch(
        "promote_supervisor_runtime.lock_held", return_value=True
    ):
        active = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=active_state,
            lock_path=Path("/tmp/fake.lock"),
        )
        drained = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state={"workers": {}, "worker_worktrees": {"leases": {}}, "queue": {"events": {}}},
            lock_path=Path("/tmp/fake.lock"),
        )

    active_gate = next(i for i in active if i["name"] == "v1_execution_authority_drained")
    drained_gate = next(i for i in drained if i["name"] == "v1_execution_authority_drained")
    assert active_gate["ok"] is False
    assert active_gate["details"]["workers"] == ["run-v1"]
    assert active_gate["details"]["leases"] == ["TASK-V1"]
    assert drained_gate["ok"] is True


def test_v1_retry_history_is_drained_but_retry_backoff_is_not() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    retry_history_state = {
        "workers": {
            "retired-chair": {
                "status": "retried",
                "request_snapshot": {"reason": "chair_review:approval"},
            },
            "retired-retry": {"status": "retry_quarantined"},
        },
        "worker_worktrees": {"leases": {}},
        "queue": {
            "events": {
                "retired-chair-event": {
                    "status": "retried",
                    "reason": "chair_review:approval",
                }
            }
        },
    }
    active_backoff_state = {
        "workers": {"retrying": {"status": "retry_backoff"}},
        "worker_worktrees": {"leases": {}},
        "queue": {"events": {}},
    }

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch(
        "promote_supervisor_runtime.lock_held", return_value=True
    ):
        drained = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=retry_history_state,
            lock_path=Path("/tmp/fake.lock"),
        )
        active = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=active_backoff_state,
            lock_path=Path("/tmp/fake.lock"),
        )

    drained_v1 = next(
        item for item in drained if item["name"] == "v1_execution_authority_drained"
    )
    drained_control = next(
        item for item in drained if item["name"] == "legacy_control_paths_drained"
    )
    active_v1 = next(
        item for item in active if item["name"] == "v1_execution_authority_drained"
    )
    assert drained_v1["ok"] is True
    assert drained_control["ok"] is True
    assert active_v1["ok"] is False
    assert active_v1["details"]["workers"] == ["retrying"]


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


def test_evaluate_promotion_invariants_rejects_legacy_control_worker_and_queue() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "workers": {
            "chair-run": {
                "run_id": "chair-run",
                "status": "running",
                "request_snapshot": {
                    "reason": "chair_review:approval_triage",
                    "metadata": {"chair": {"mode": "chair_review"}},
                },
            }
        },
        "queue": {
            "events": {
                "planning-event": {
                    "status": "queued",
                    "reason": "discussion_planning_round",
                }
            }
        },
    }
    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch(
        "promote_supervisor_runtime.lock_held", return_value=True
    ):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status={"tasks": []},
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    legacy = next(
        item for item in invariants if item["name"] == "legacy_control_paths_drained"
    )
    assert legacy["ok"] is False
    assert legacy["details"] == {
        "workers": ["chair-run"],
        "queue_events": ["planning-event"],
    }


def test_evaluate_promotion_invariants_accepts_inactive_unavailable_provider() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    config = {
        "agents": {
            "claude": {"provider": "claude", "max_parallel": 1},
            "inactive_provider": {
                "provider": "inactive_provider",
                "max_parallel": 0,
            },
        },
        "providers": {
            "claude": {"enabled": True, "account": "claude"},
            "inactive_provider": {
                "enabled": True,
                "account": "inactive_provider",
            },
        },
        "ready_dispatcher": {
            "target_workload": {"claude": 5},
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


def test_evaluate_promotion_invariants_accepts_retry_backoff_worker() -> None:
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w_retry": {
                "status": "retry_backoff",
                "current_task_id": "T1",
                "queue_event_id": "evt1",
                "run_id": "run_retry",
                "runner_finished_at": "2026-08-09T18:00:00Z",
            },
        },
        "queue": {
            "events": {
                "evt1": {
                    "id": "evt1",
                    "task_id": "T1",
                    "status": "retry_backoff",
                    "run_id": "run_retry",
                    "lease_owner": "run_retry",
                }
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
    assert worker_inv["ok"] is True
    assert worker_inv["details"]["reasons"] == []


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
    full_preflight: bool = False,
) -> tuple[Path, Path, Path, str, str, bytes]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--initial-branch=dev")
    _git(source, "config", "user.name", "Promotion Test")
    _git(source, "config", "user.email", "promotion@example.test")
    (source / "README.md").write_text("trusted candidate\n", encoding="utf-8")
    if full_preflight:
        fixture_config, fixture_state, _fixture_status, _fixture_providers = (
            create_realistic_healthy_fixture(source)
        )
        fixture_state["supervisor"]["pid"] = 7171
        write_json(Path(fixture_config["paths"]["state_file"]), fixture_state)
        Path(fixture_config["paths"]["state_file"]).with_name(
            "supervisor.pid"
        ).write_text("7171\n", encoding="utf-8")
        source_specs = [
            (source / ".orchestrator" / "supervisor.py", False),
            (source / ".orchestrator" / "supervisor_watchdog.py", True),
            (source / "scripts" / "run-supervisor-watchdog.sh", True),
            (source / "scripts" / "sync-dev-root.sh", True),
            (source / "scripts" / "ai-status.sh", True),
            (source / "scripts" / "ai_status.py", False),
            (source / "scripts" / "provision_live_supervisor_config.py", False),
        ]
        for source_path, executable_source in source_specs:
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                f"# persistent launch fixture: {source_path.name}\n",
                encoding="utf-8",
            )
            if executable_source:
                source_path.chmod(0o755)
        _git(source, "add", ".")
    else:
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

    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    live_config.parent.mkdir()
    if full_preflight:
        status_root = tmp_path / "source-status-root"
        (status_root / ".orchestrator" / "logs").mkdir(
            parents=True,
            exist_ok=True,
        )
        event_log = live_config.parent / "task-state-events.jsonl"
        event_log.write_text("", encoding="utf-8")
        write_json(
            event_log.with_name(f"{event_log.name}.head.json"),
            {"sequence": 0, "state": {"tasks": []}},
        )
        worker_worktree_root = tmp_path / "worker-worktrees"
        worker_worktree_root.mkdir()
        executable = Path(sys.executable).resolve()
        live_config_payload = {
            "watchdog": {
                "supervisor_command": [
                    str(executable),
                    "-u",
                    str(candidate / ".orchestrator" / "supervisor.py"),
                    "--config",
                    str(live_config),
                    "--verbose",
                ]
            },
            "paths": {
                "status_file": str(status_root / "ai-status.json"),
                "state_file": str(status_root / ".orchestrator" / "state.json"),
                "provider_capabilities": str(
                    status_root / ".orchestrator" / "provider_capabilities.json"
                ),
                "event_queue": str(
                    status_root / ".orchestrator" / "event-queue.jsonl"
                ),
            },
            "task_state_store": {
                "mode": "authoritative",
                "event_log": str(event_log),
            },
            "worker_worktrees": {"root": str(worker_worktree_root)},
            "providers": fixture_config["providers"],
        }
        config_bytes = (
            json.dumps(live_config_payload, sort_keys=True) + "\n"
        ).encode("utf-8")
    else:
        config_bytes = b'{"runtime":"immutable","writes":false}\n'
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


def _persistent_process_reader(
    identity: CandidateRuntimeIdentity,
) -> InjectedRuntimeProcessReader:
    config = json.loads(identity.config_bytes)
    argv = tuple(config["watchdog"]["supervisor_command"])
    status_root = Path(config["paths"]["status_file"]).parent
    generation = ProcessGeneration(pid=7171, starttime_ticks=818181, state="S")
    root_stat = identity.candidate_root.stat()
    lock = SupervisorAdmissionLockIdentity(
        path=status_root / ".orchestrator" / "supervisor.lock",
        device=91,
        inode=92,
        byte_length=5,
        sha256="d" * 64,
        mtime_ns=93,
        ctime_ns=94,
        kernel_lock_id="95",
        kernel_lock_kind="FLOCK",
        kernel_lock_class="ADVISORY",
        kernel_lock_mode="WRITE",
        kernel_lock_start="0",
        kernel_lock_end="EOF",
        owner_pid=generation.pid,
        owner_starttime_ticks=generation.starttime_ticks,
    )
    environment = {
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": os.environ[
            "BRIDGE_SIGNING_PUBLIC_KEYS_JSON"
        ],
        "PANTHEON_COMMAND_ROOT": str(identity.candidate_root),
        "PANTHEON_COMMAND_RUNTIME_SHA": identity.head_commit,
        "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON": os.environ[
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON"
        ],
        "PANTHEON_STATUS_ROOT": str(status_root),
    }
    supervisor_index = next(
        index
        for index, argument in enumerate(argv)
        if Path(argument).name == "supervisor.py"
    )
    if "-B" in argv[1:supervisor_index]:
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return InjectedRuntimeProcessReader(
        pids=(generation.pid,),
        generations={generation.pid: generation},
        argv={generation.pid: argv},
        executable={generation.pid: Path(argv[0]).resolve()},
        cwd={
            generation.pid: ProcessCwdIdentity(
                path=identity.candidate_root,
                device=root_stat.st_dev,
                inode=root_stat.st_ino,
            )
        },
        environment={generation.pid: environment},
        locks=[lock, lock],
    )


def test_discover_only_accepts_valid_persistent_command_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path, full_preflight=True)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent,
        remote,
        live_config,
    )
    now = datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc)
    config_before = live_config.read_bytes()
    state_path = tmp_path / "source-status-root" / ".orchestrator" / "state.json"
    state_before = state_path.read_bytes()

    with parent_patch, remote_patch, config_patch:
        identity = build_candidate_runtime_identity(candidate)
        reader = _persistent_process_reader(identity)
        with patch(
            "promote_supervisor_runtime.ProcfsRuntimeProcessReader",
            return_value=reader,
        ), patch(
            "promote_supervisor_runtime.lock_held",
            return_value=True,
        ), patch(
            "promote_supervisor_runtime.pid_is_alive",
            return_value=True,
        ), patch(
            "supervisor_runtime_health.lock_held",
            return_value=True,
        ), patch(
            "supervisor_runtime_health.pid_matches_supervisor",
            return_value=True,
        ), patch(
            "supervisor_runtime_health.pid_is_alive",
            return_value=True,
        ), patch(
            "promote_supervisor_runtime.datetime"
        ) as datetime_mock:
            datetime_mock.now.return_value = now
            datetime_mock.side_effect = lambda *args, **kwargs: datetime(
                *args, **kwargs
            )
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "promote_supervisor_runtime.py",
                    "--discover-only",
                    "--json",
                    "--repo",
                    str(candidate),
                    "--config-path",
                    str(candidate / ".orchestrator" / "config.json"),
                ],
            )
            exit_code = promotion.main()

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0, [
        item for item in payload["invariants"] if not item["ok"]
    ]
    assert payload["preflight_mode"] == "discover_only"
    assert payload["eligible_for_promotion"] is True
    assert payload["candidate_runtime_identity"]["candidate_root"] == str(candidate)
    assert payload["incumbent_supervisor_process_identity"]["pid"] == 7171
    assert payload["governed_supervisor_launch_contract"]["cwd"] == str(candidate)
    assert payload["identity_revalidation_stages"] == [
        "after_root_git_discovery",
        "after_process_discovery",
        "after_launch_contract_assembly",
        "final_preflight_readback",
    ]
    assert live_config.read_bytes() == config_before
    assert state_path.read_bytes() == state_before
    assert _git(candidate, "status", "--porcelain") == ""


def test_discover_only_rejects_temporary_reviewer_worktree(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path, full_preflight=True)
    )
    reviewer_worktree = tmp_path / "pantheon-runtime-promotion-review-pr4433"
    subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "worktree",
            "add",
            "--detach",
            str(reviewer_worktree),
            "HEAD",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent,
        remote,
        live_config,
    )

    with parent_patch, remote_patch, config_patch, patch(
        "promote_supervisor_runtime.ProcfsRuntimeProcessReader"
    ) as proc_reader:
        snapshot = capture_promotion_snapshot(
            reviewer_worktree,
            config_path_arg=reviewer_worktree
            / ".orchestrator"
            / "config.json",
            now=datetime(2026, 6, 6, 6, 30, tzinfo=timezone.utc),
        )

    identity_invariant = next(
        invariant
        for invariant in snapshot["invariants"]
        if invariant["name"] == "candidate_runtime_identity_immutable"
    )
    assert snapshot["eligible_for_promotion"] is False
    assert "direct child" in identity_invariant["details"]["error"]
    assert snapshot["governed_supervisor_launch_contract"] is None
    proc_reader.assert_not_called()






def test_candidate_identity_survives_closed_standard_stream_descriptors(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    proof_path = tmp_path / "closed-stdio-proof"
    child = """
import os
import sys
from pathlib import Path
from unittest.mock import patch

import promote_supervisor_runtime as promotion

for descriptor in (0, 1, 2):
    try:
        os.close(descriptor)
    except OSError:
        pass

candidate = Path(sys.argv[1])
parent = Path(sys.argv[2])
proof_path = Path(sys.argv[3])
with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
    handle = promotion._open_candidate_root_handle(candidate)
    try:
        assert min(
            handle.descriptor,
            handle.git_descriptor,
            handle.git_objects_descriptor,
            handle.git_config_descriptor,
            handle.git_head_descriptor,
            handle.git_index_descriptor,
        ) >= 3
        assert promotion.parse_origin_url(handle)
    finally:
        promotion._close_candidate_root_handle(handle)
proof_path.write_text("validated\\n", encoding="utf-8")
"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(candidate),
            str(parent),
            str(proof_path),
        ],
        cwd=Path(__file__).resolve().parent,
        check=False,
        close_fds=True,
    )

    assert result.returncode == 0
    assert proof_path.read_text(encoding="utf-8") == "validated\n"


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


def _remove_gitlink_worktree(candidate: Path, relative_path: str = "lean") -> Path:
    gitlink = candidate / relative_path
    if gitlink.is_symlink() or gitlink.is_file():
        gitlink.unlink()
    elif gitlink.exists():
        shutil.rmtree(gitlink)
    return gitlink


def test_git_identity_allows_absent_mode_160000_gitlink(tmp_path: Path) -> None:
    candidate, parent, _remote, _commit, tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    assert _git(candidate, "ls-files", "--stage", "lean").startswith("160000 ")
    _remove_gitlink_worktree(candidate)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        assert verify_working_tree_cleanliness(candidate) == tree


def test_git_identity_allows_empty_direct_mode_160000_gitlink(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    gitlink.mkdir()

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        assert verify_working_tree_cleanliness(candidate) == tree


def test_git_identity_rejects_regular_file_at_mode_160000_gitlink(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    gitlink.write_text("hidden payload\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Tracked gitlink worktree.*wrong type"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_symlink_at_mode_160000_gitlink(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    external = tmp_path / "external-gitlink"
    external.mkdir()
    gitlink.symlink_to(external, target_is_directory=True)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Tracked gitlink worktree.*symlink"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_file_hidden_below_mode_160000_gitlink(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    gitlink.mkdir()
    (gitlink / "payload.py").write_text("raise SystemExit\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="must be absent or an empty"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_nested_directory_below_mode_160000_gitlink(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    (gitlink / "nested").mkdir(parents=True)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="must be absent or an empty"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_initialized_metadata_below_gitlink(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    (gitlink / ".git" / "objects").mkdir(parents=True)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="must be absent or an empty"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_external_metadata_gitfile_below_gitlink(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    gitlink.mkdir()
    external_metadata = tmp_path / "external-submodule-metadata"
    external_metadata.mkdir()
    (gitlink / ".git").write_text(
        f"gitdir: {external_metadata}\n",
        encoding="utf-8",
    )

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="must be absent or an empty"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_symlinked_parent_component_for_nested_gitlink(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path, gitlink_path="vendor/lean")
    )
    vendor = candidate / "vendor"
    if vendor.exists() or vendor.is_symlink():
        shutil.rmtree(vendor)
    external_vendor = tmp_path / "external-vendor"
    (external_vendor / "lean").mkdir(parents=True)
    vendor.symlink_to(external_vendor, target_is_directory=True)

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="path component.*symlink"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_gitlink_tree_index_identity_mismatch(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    _git(
        candidate,
        "update-index",
        "--cacheinfo",
        f"160000,{commit},lean",
    )

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="gitlink tree/index identity mismatch"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_cross_filesystem_gitlink_directory(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    gitlink.mkdir()
    gitlink_inode = gitlink.stat().st_ino
    real_identity = promotion._filesystem_identity

    def cross_filesystem_identity(descriptor: int) -> FilesystemIdentity:
        identity = real_identity(descriptor)
        if identity.inode == gitlink_inode:
            return replace(identity, device=identity.device + 1)
        return identity

    with patch(
        "promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX",
        parent,
    ), patch(
        "promote_supervisor_runtime._filesystem_identity",
        side_effect=cross_filesystem_identity,
    ):
        with pytest.raises(ValueError, match="gitlink.*escaped.*filesystem"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_concurrent_gitlink_directory_replacement(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    gitlink = _remove_gitlink_worktree(candidate)
    gitlink.mkdir()
    original_assert = promotion._assert_relative_identity
    replaced = False

    def replace_before_identity_check(*args: Any, **kwargs: Any) -> None:
        nonlocal replaced
        if kwargs.get("label") == "Tracked gitlink worktree 'lean'" and not replaced:
            replaced = True
            old_gitlink = candidate / "lean-old"
            gitlink.rename(old_gitlink)
            gitlink.mkdir()
        original_assert(*args, **kwargs)

    with patch(
        "promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX",
        parent,
    ), patch(
        "promote_supervisor_runtime._assert_relative_identity",
        side_effect=replace_before_identity_check,
    ):
        with pytest.raises(ValueError, match="gitlink.*identity changed"):
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


def test_git_identity_allows_verified_supervisor_runtime_log_directory(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    logs = candidate / ".orchestrator" / "logs"
    logs.mkdir(parents=True)
    (logs / "20260810T023847615369Z-codex-codex1_1-28cc7f.log").write_text(
        "governed worker output\n",
        encoding="utf-8",
    )
    exclude = candidate / ".git" / "info" / "exclude"
    exclude.write_text("/.orchestrator/logs/\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        assert verify_working_tree_cleanliness(candidate) == tree


@pytest.mark.parametrize("unsafe_entry", ["payload.py", "nested/payload.log"])
def test_git_identity_rejects_unsafe_runtime_log_directory_entry(
    tmp_path: Path,
    unsafe_entry: str,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    unsafe = candidate / ".orchestrator" / "logs" / unsafe_entry
    unsafe.parent.mkdir(parents=True, exist_ok=True)
    unsafe.write_text("not a runtime log\n", encoding="utf-8")
    exclude = candidate / ".git" / "info" / "exclude"
    exclude.write_text("/.orchestrator/logs/\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="Forbidden entry|wrong type"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_executable_runtime_log(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    generated = candidate / ".orchestrator" / "logs" / "worker.log"
    generated.parent.mkdir(parents=True)
    generated.write_text("executable debris\n", encoding="utf-8")
    generated.chmod(0o755)
    exclude = candidate / ".git" / "info" / "exclude"
    exclude.write_text("/.orchestrator/logs/\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="must not be executable"):
            verify_working_tree_cleanliness(candidate)


def test_git_identity_rejects_symlinked_runtime_log(
    tmp_path: Path,
) -> None:
    candidate, parent, _remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path)
    )
    external = tmp_path / "external.log"
    external.write_text("external content\n", encoding="utf-8")
    generated = candidate / ".orchestrator" / "logs" / "worker.log"
    generated.parent.mkdir(parents=True)
    generated.symlink_to(external)
    exclude = candidate / ".git" / "info" / "exclude"
    exclude.write_text("/.orchestrator/logs/\n", encoding="utf-8")

    with patch("promote_supervisor_runtime.ALLOWED_COMMAND_RUNTIMES_PREFIX", parent):
        with pytest.raises(ValueError, match="symlink|wrong type"):
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


def test_config_variants_are_derived_from_one_capture_without_external_write(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, _tree, config_bytes = (
        _make_candidate_fixture(tmp_path, full_preflight=True)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        identity = build_candidate_runtime_identity(candidate)
        candidate_variant = promotion.derive_supervisor_config_variant(
            identity,
            command_root=candidate,
        )
        rollback_root = parent / ("f" * 40)
        rollback_variant = promotion.derive_supervisor_config_variant(
            identity,
            command_root=rollback_root,
        )

    assert live_config.read_bytes() == config_bytes
    assert candidate_variant.supervisor_argv != rollback_variant.supervisor_argv
    assert str(candidate / ".orchestrator" / "supervisor.py") in (
        candidate_variant.supervisor_argv
    )
    assert str(rollback_root / ".orchestrator" / "supervisor.py") in (
        rollback_variant.supervisor_argv
    )
    candidate_entrypoint = candidate_variant.supervisor_argv.index(
        str(candidate / ".orchestrator" / "supervisor.py")
    )
    rollback_entrypoint = rollback_variant.supervisor_argv.index(
        str(rollback_root / ".orchestrator" / "supervisor.py")
    )
    assert candidate_variant.supervisor_argv[candidate_entrypoint - 1] == "-B"
    assert rollback_variant.supervisor_argv[rollback_entrypoint - 1] == "-B"
    assert candidate_variant.sha256 == hashlib.sha256(
        candidate_variant.content
    ).hexdigest()
    assert rollback_variant.sha256 == hashlib.sha256(
        rollback_variant.content
    ).hexdigest()


def test_config_variant_keeps_existing_no_bytecode_flag_idempotent(
    tmp_path: Path,
) -> None:
    candidate, parent, remote, _commit, _tree, _config_bytes = (
        _make_candidate_fixture(tmp_path, full_preflight=True)
    )
    live_config = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    parent_patch, remote_patch, config_patch = _identity_policy_patches(
        parent, remote, live_config
    )
    with parent_patch, remote_patch, config_patch:
        identity = build_candidate_runtime_identity(candidate)
    payload = json.loads(identity.config_bytes)
    command = payload["watchdog"]["supervisor_command"]
    entrypoint_index = next(
        index
        for index, argument in enumerate(command)
        if Path(argument).name == "supervisor.py"
    )
    command.insert(entrypoint_index, "-B")
    config_bytes = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    identity = replace(
        identity,
        config_bytes=config_bytes,
        config_byte_length=len(config_bytes),
        config_sha256=hashlib.sha256(config_bytes).hexdigest(),
    )

    variant = promotion.derive_supervisor_config_variant(
        identity,
        command_root=identity.candidate_root,
    )

    assert variant.supervisor_argv.count("-B") == 1


def _config_install_fixture(
    tmp_path: Path,
) -> tuple[CandidateRuntimeIdentity, Path, bytes, SupervisorConfigVariant]:
    identity, live_config, original = _build_test_candidate_identity(tmp_path)
    target = b'{"generation":"candidate"}\n'
    variant = SupervisorConfigVariant(
        command_root=identity.candidate_root,
        supervisor_argv=("python3", "supervisor.py"),
        content=target,
        byte_length=len(target),
        sha256=hashlib.sha256(target).hexdigest(),
    )
    return identity, live_config, original, variant


@pytest.mark.parametrize(
    "fault_stage,replaced",
    [
        ("after_temp_fsync", False),
        ("before_replace", False),
        ("after_replace", True),
        ("after_directory_fsync", True),
    ],
)
def test_atomic_config_install_crash_windows_never_expose_partial_bytes(
    tmp_path: Path,
    fault_stage: str,
    replaced: bool,
) -> None:
    identity, live_config, original, variant = _config_install_fixture(tmp_path)

    def fail(stage: str) -> None:
        if stage == fault_stage:
            raise OSError(f"injected {stage}")

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(OSError, match=fault_stage):
            promotion.atomic_install_live_config(
                identity,
                variant,
                allowed_predecessors={
                    hashlib.sha256(original).hexdigest(): original
                },
                fault_hook=fail,
            )

    assert live_config.read_bytes() == (variant.content if replaced else original)
    assert not tuple(live_config.parent.glob(f".{live_config.name}.promotion-*"))


def test_atomic_config_install_rejects_last_moment_replacement_race(
    tmp_path: Path,
) -> None:
    identity, live_config, original, variant = _config_install_fixture(tmp_path)
    unknown = b'{"generation":"unknown-writer"}\n'

    def race(stage: str) -> None:
        if stage == "before_replace":
            live_config.write_bytes(unknown)

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with pytest.raises(ValueError, match="raced at atomic replacement"):
            promotion.atomic_install_live_config(
                identity,
                variant,
                allowed_predecessors={
                    hashlib.sha256(original).hexdigest(): original
                },
                fault_hook=race,
            )

    assert live_config.read_bytes() == unknown


def test_atomic_config_install_directory_fsync_failure_is_fatal(
    tmp_path: Path,
) -> None:
    identity, live_config, original, variant = _config_install_fixture(tmp_path)
    real_fsync = os.fsync

    def fail_directory(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    with patch("promote_supervisor_runtime.LIVE_SUPERVISOR_CONFIG_PATH", live_config):
        with patch("promote_supervisor_runtime.os.fsync", side_effect=fail_directory):
            with pytest.raises(OSError, match="directory fsync failure"):
                promotion.atomic_install_live_config(
                    identity,
                    variant,
                    allowed_predecessors={
                        hashlib.sha256(original).hexdigest(): original
                    },
                )

    assert live_config.read_bytes() == variant.content


class InjectedRuntimeProcessReader:
    def __init__(
        self,
        *,
        pids: tuple[int, ...],
        generations: dict[int, ProcessGeneration],
        argv: dict[int, tuple[str, ...]],
        executable: dict[int, Path],
        cwd: dict[int, ProcessCwdIdentity],
        environment: dict[int, dict[str, str]],
        locks: list[SupervisorAdmissionLockIdentity],
    ) -> None:
        self.pids = pids
        self.generations = generations
        self.generation_sequences: dict[
            int, list[ProcessGeneration | BaseException]
        ] = {}
        self.argv = argv
        self.executable = executable
        self.cwd = cwd
        self.environment = environment
        self.locks = locks
        self.errors: dict[str, BaseException] = {}

    def list_pids(self) -> tuple[int, ...]:
        return self.pids

    def read_generation(self, pid: int) -> ProcessGeneration:
        sequence = self.generation_sequences.get(pid)
        if sequence:
            value = sequence.pop(0) if len(sequence) > 1 else sequence[0]
            if isinstance(value, BaseException):
                raise value
            return value
        return self.generations[pid]

    def _raise(self, name: str) -> None:
        error = self.errors.get(name)
        if error is not None:
            raise error

    def read_argv(self, pid: int) -> tuple[str, ...]:
        self._raise("read_argv")
        return self.argv[pid]

    def read_executable(self, pid: int) -> Path:
        self._raise("read_executable")
        return self.executable[pid]

    def read_cwd(self, pid: int) -> ProcessCwdIdentity:
        self._raise("read_cwd")
        return self.cwd[pid]

    def read_environment_contract(self, pid: int) -> dict[str, str]:
        self._raise("read_environment_contract")
        return dict(self.environment[pid])

    def read_admission_lock(
        self,
        path: Path,
    ) -> SupervisorAdmissionLockIdentity:
        self._raise("read_admission_lock")
        value = self.locks.pop(0) if len(self.locks) > 1 else self.locks[0]
        return value


def _injected_process_fixture(
    tmp_path: Path,
) -> tuple[
    CandidateRuntimeIdentity,
    InjectedRuntimeProcessReader,
    tuple[str, ...],
]:
    candidate = tmp_path / "candidate"
    (candidate / ".orchestrator").mkdir(parents=True)
    entrypoint = candidate / ".orchestrator" / "supervisor.py"
    entrypoint.write_text("# test entrypoint\n", encoding="utf-8")
    source_specs = [
        (candidate / ".orchestrator" / "supervisor_watchdog.py", True),
        (candidate / "scripts" / "run-supervisor-watchdog.sh", True),
        (candidate / "scripts" / "sync-dev-root.sh", True),
        (candidate / "scripts" / "ai-status.sh", True),
        (candidate / "scripts" / "ai_status.py", False),
        (candidate / "scripts" / "provision_live_supervisor_config.py", False),
    ]
    for source_path, executable_source in source_specs:
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            f"# test launch source: {source_path.name}\n",
            encoding="utf-8",
        )
        if executable_source:
            source_path.chmod(0o755)
    candidate_stat = candidate.stat()
    config_path = tmp_path / "runtime" / "live-supervisor-mainroot-config.json"
    config_path.parent.mkdir()
    status_root = tmp_path / "status-root"
    (status_root / ".orchestrator").mkdir(parents=True)
    status_file = status_root / "ai-status.json"
    state_file = status_root / ".orchestrator" / "state.json"
    log_directory = status_root / ".orchestrator" / "logs"
    log_directory.mkdir(parents=True)
    status_file.write_text("{}\n", encoding="utf-8")
    state_file.write_text("{}\n", encoding="utf-8")
    event_log = config_path.parent / "task-state-events.jsonl"
    event_log.write_text("", encoding="utf-8")
    worker_worktree_root = tmp_path / "worker-worktrees"
    worker_worktree_root.mkdir()
    executable = Path(sys.executable).resolve()
    argv = (
        str(executable),
        "-u",
        "-B",
        str(entrypoint),
        "--config",
        str(config_path),
        "--verbose",
    )
    config_bytes = json.dumps(
        {
            "watchdog": {"supervisor_command": list(argv)},
            "paths": {
                "status_file": str(status_file),
                "state_file": str(state_file),
            },
            "task_state_store": {
                "mode": "authoritative",
                "event_log": str(event_log),
            },
            "worker_worktrees": {"root": str(worker_worktree_root)},
        },
        sort_keys=True,
    ).encode("utf-8")
    config_path.write_bytes(config_bytes)
    config_stat = config_path.stat()
    commit = "a" * 40
    tree = "b" * 40
    identity = CandidateRuntimeIdentity(
        candidate_root=candidate,
        candidate_root_device=candidate_stat.st_dev,
        candidate_root_inode=candidate_stat.st_ino,
        git_directory_device=1,
        git_directory_inode=2,
        git_objects_device=3,
        git_objects_inode=4,
        git_config_device=5,
        git_config_inode=6,
        git_head_device=7,
        git_head_inode=8,
        git_index_device=9,
        git_index_inode=10,
        basename=commit,
        head_commit=commit,
        tracked_tree_identity=tree,
        accepted_dev_commit=commit,
        remote_url="https://github.com/ajoe734/pantheon.git",
        canonical_remote="github.com/ajoe734/pantheon",
        repository_slug="ajoe734/pantheon",
        config_path=config_path,
        config_device=config_stat.st_dev,
        config_inode=config_stat.st_ino,
        config_path_components=(),
        config_bytes=config_bytes,
        config_byte_length=len(config_bytes),
        config_sha256=promotion.hashlib.sha256(config_bytes).hexdigest(),
    )
    generation = ProcessGeneration(pid=1717, starttime_ticks=424242, state="S")
    lock = SupervisorAdmissionLockIdentity(
        path=status_root / ".orchestrator" / "supervisor.lock",
        device=30,
        inode=31,
        byte_length=5,
        sha256="c" * 64,
        mtime_ns=32,
        ctime_ns=33,
        kernel_lock_id="71",
        kernel_lock_kind="FLOCK",
        kernel_lock_class="ADVISORY",
        kernel_lock_mode="WRITE",
        kernel_lock_start="0",
        kernel_lock_end="EOF",
        owner_pid=generation.pid,
        owner_starttime_ticks=generation.starttime_ticks,
    )
    reader = InjectedRuntimeProcessReader(
        pids=(generation.pid,),
        generations={generation.pid: generation},
        argv={generation.pid: argv},
        executable={generation.pid: executable},
        cwd={
            generation.pid: ProcessCwdIdentity(
                path=candidate,
                device=candidate_stat.st_dev,
                inode=candidate_stat.st_ino,
            )
        },
        environment={
            generation.pid: {
                "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": os.environ[
                    "BRIDGE_SIGNING_PUBLIC_KEYS_JSON"
                ],
                "PANTHEON_COMMAND_ROOT": str(candidate),
                "PANTHEON_COMMAND_RUNTIME_SHA": commit,
                "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON": os.environ[
                    "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON"
                ],
                "PANTHEON_STATUS_ROOT": str(status_root),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        },
        locks=[lock, lock],
    )
    return identity, reader, argv


def _discover_injected(
    identity: CandidateRuntimeIdentity,
    reader: InjectedRuntimeProcessReader,
    *,
    git_identity: tuple[str, str] | None = None,
) -> SupervisorProcessIdentity:
    return discover_incumbent_supervisor_process(
        identity,
        reader=reader,
        cwd_git_identity_reader=(
            lambda _cwd: git_identity
            if git_identity is not None
            else (identity.head_commit, identity.tracked_tree_identity)
        ),
    )


def _replace_identity_live_config(
    identity: CandidateRuntimeIdentity,
    update: Any,
) -> CandidateRuntimeIdentity:
    payload = json.loads(identity.config_bytes)
    update(payload)
    config_bytes = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    identity.config_path.write_bytes(config_bytes)
    config_stat = identity.config_path.stat()
    return replace(
        identity,
        config_device=config_stat.st_dev,
        config_inode=config_stat.st_ino,
        config_bytes=config_bytes,
        config_byte_length=len(config_bytes),
        config_sha256=promotion.hashlib.sha256(config_bytes).hexdigest(),
    )


def _mutable_binding_stub(
    identity: CandidateRuntimeIdentity,
) -> tuple[Any, ...]:
    remote = validate_remote_url("https://github.com/ajoe734/pantheon.git")
    return (
        identity.head_commit,
        identity.tracked_tree_identity,
        promotion.TrustedDevIdentity(
            commit=identity.accepted_dev_commit,
            candidate_commit_tree=identity.tracked_tree_identity,
        ),
        "https://github.com/ajoe734/pantheon.git",
        remote,
        (),
        (),
    )






















LIVE_FLEET_BRIEF_1364_BYTES: bytes = (
    b"# Task Brief: SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT-20260806\n\n"
    b"This file is generated by the orchestrator for task-scoped execution context.\n"
    b"Treat `ai-status.json` as the durable execution source of truth only when you need to verify or update state.\n"
    b"Do not read `current-work.md` by default for implementation context.\n\n"
    b"## Task\n"
    b"- Title: Commit the missing supervisor dispatch refactor proposal doc\n"
    b"- Status: review\n"
    b"- Owner: Antigravity\n"
    b"- Reviewer: Codex2\n"
    b"- Phase: Supervisor Dispatch Reliability\n"
    b"- Last update: 2026-08-09T08:23:22Z\n"
    b"- Next: Supervisor recorded worker failure streak 1/2 for SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT-20260806.\n\n"
    b"## Summary\n"
    b"-\n\n"
    b"## Dependencies\n"
    b"- none\n\n"
    b"## Artifacts\n"
    b"- docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md\n\n"
    b"## Recent Task Activity\n"
    b"- Omitted from automatic dispatch context. The canonical task row above is the bounded handoff context; query validated activity history only for targeted forensic work.\n\n"
    b"## Relevant Canonical Files\n"
    b"- AI_COLLABORATION_GUIDE.md\n"
    b"- ai-status.json\n"
    b"- docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md\n\n"
    b"## Working Rules\n"
    b"- Use scripts/ai-status.sh or python3 scripts/ai_status.py for status changes.\n"
    b"- Keep execution updates short and structured.\n"
    b"- If you need raw provider/debug details, ask for the relevant runtime log or evidence ref instead of scanning global summaries.\n"
)

LIVE_FLEET_BRIEF_2132_BYTES: bytes = (
    b"# Task Brief: SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801\n\n"
    b"This file is generated by the orchestrator for task-scoped execution context.\n"
    b"Treat `ai-status.json` as the durable execution source of truth only when you need to verify or update state.\n"
    b"Do not read `current-work.md` by default for implementation context.\n\n"
    b"## Task\n"
    b"- Title: Gate fleet bootstrap on exact runtime root coherence\n"
    b"- Status: review\n"
    b"- Owner: Antigravity\n"
    b"- Reviewer: Codex2\n"
    b"- Phase: Twelve Loop / Fleet Bootstrap Gate\n"
    b"- Last update: 2026-08-09T11:08:05Z\n"
    b"- Next: Created fresh open evidence-correction PR #4666 (head d46d68a709db1d4f1af062dde3f09fbfd482a056) for independent review by Codex2.\n\n"
    b"## Summary\n"
    b"\xe5\x9c\xa8\xe4\xbb\xbb\xe4\xbd\x95 L12 \xe9\x87\x8d\xe7\x9b\xa4\xe9\xbb\x9e\xe6\x88\x96 25-task admission \xe5\x89\x8d\xef\xbc\x8c\xe8\xad\x89\xe6\x98\x8e supervisor\xe3\x80\x81watchdog\xe3\x80\x81worker runner\xe3\x80\x81command runtime\xe3\x80\x81Git source root \xe8\x88\x87 status root \xe5\x90\x84\xe8\x87\xaa\xe7\xb6\x81\xe5\xae\x9a\xe6\xad\xa3\xe7\xa2\xba\xe4\xb8\x94\xe7\x9c\x9f\xe5\xaf\xa6 auto-worker \xe5\x8f\xaf\xe6\x8c\x81\xe7\xba\x8c\xe9\x81\x8b\xe8\xa1\x8c\xe3\x80\x82\n\n"
    b"## Dependencies\n"
    b"- SUP-WORKER-WORKTREE-SOURCE-ROOT-20260730: done \xc2\xb7 Restore owner Claude and reviewer Antigravity\n"
    b"- SUP-RUNTIME-IDENTITY-ROOT-CONFIG-GIT-V2-20260801: done \xc2\xb7 Build immutable runtime root, config, and Git identity guards\n"
    b"- SUP-RUNTIME-IDENTITY-PROCESS-BINDING-V2-20260801: done \xc2\xb7 Bind one incumbent supervisor process to immutable runtime identity\n"
    b"- SUP-RUNTIME-IDENTITY-LAUNCH-PREFLIGHT-V2-20260801: done \xc2\xb7 Compose governed launch identity and full discover-only preflight\n\n"
    b"## Artifacts\n"
    b"- docs/deployment/evidence/twelve-loop-gap/SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801/\n\n"
    b"## Recent Task Activity\n"
    b"- Omitted from automatic dispatch context. The canonical task row above is the bounded handoff context; query validated activity history only for targeted forensic work.\n\n"
    b"## Relevant Canonical Files\n"
    b"- AI_COLLABORATION_GUIDE.md\n"
    b"- ai-status.json\n"
    b"- docs/deployment/evidence/twelve-loop-gap/SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801/\n\n"
    b"## Working Rules\n"
    b"- Use scripts/ai-status.sh or python3 scripts/ai_status.py for status changes.\n"
    b"- Keep execution updates short and structured.\n"
    b"- If you need raw provider/debug details, ask for the relevant runtime log or evidence ref instead of scanning global summaries.\n"
)


def _legacy_mutable_task_brief_drift_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Any]:
    """Create one accepted mutable root with an old generated brief overwrite."""
    _candidate, _parent, remote, _commit, _tree, _config = (
        _make_candidate_fixture(tmp_path, full_preflight=True)
    )
    source = tmp_path / "source"
    status_file = source / "ai-status.json"
    status_file.write_text(
        json.dumps({
            "tasks": [
                {
                    "id": "SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT-20260806",
                    "title": "Commit the missing supervisor dispatch refactor proposal doc",
                    "owner": "Antigravity",
                    "reviewer": "Codex2",
                }
            ]
        }),
        encoding="utf-8",
    )
    common_py = source / ".orchestrator" / "common.py"
    tracked_brief = (
        source
        / ".orchestrator"
        / "task-briefs"
        / "sup_dispatch_refactor_proposal_doc_commit_20260806.md"
    )
    tracked_brief.parent.mkdir(parents=True, exist_ok=True)
    common_py.write_text(
        "from pathlib import Path\n"
        "TASK_BRIEFS_DIR = None\n"
        "def write_task_brief(config, task_id):\n"
        "    path = TASK_BRIEFS_DIR / f'{task_id.lower().replace(\"-\", \"_\")}.md'\n"
        "    path.write_text('orchestrator-regenerated task brief\\n', encoding='utf-8')\n"
        "    return path\n",
        encoding="utf-8",
    )
    tracked_brief.write_text("committed task brief in git tree\n", encoding="utf-8")
    _git(source, "add", str(status_file.relative_to(source)))
    _git(source, "add", str(common_py.relative_to(source)))
    _git(source, "add", str(tracked_brief.relative_to(source)))
    _git(source, "commit", "-m", "track orchestrator task brief")
    _git(source, "push", str(remote), "dev:dev")
    commit = _git(source, "rev-parse", "HEAD")
    _git(
        source,
        "remote",
        "add",
        "origin",
        "https://github.com/ajoe734/pantheon.git",
    )
    mutable_root = tmp_path / "dev-root"
    _git(source, "worktree", "add", "--detach", str(mutable_root), commit)
    regenerated_brief = mutable_root / tracked_brief.relative_to(source)
    regenerated_brief.write_bytes(LIVE_FLEET_BRIEF_1364_BYTES)
    return remote, mutable_root, regenerated_brief, mutable_root.stat()







































class InjectedLaunchFilesystem(promotion.OSLaunchFilesystem):
    def __init__(
        self,
        *,
        unwritable_directories: set[Path] | None = None,
        unwritable_files: set[Path] | None = None,
    ) -> None:
        self.unwritable_directories = unwritable_directories or set()
        self.unwritable_files = unwritable_files or set()

    def directory_is_writable(self, path: Path) -> bool:
        if path in self.unwritable_directories:
            return False
        return super().directory_is_writable(path)

    def file_is_writable(self, path: Path) -> bool:
        if path in self.unwritable_files:
            return False
        return super().file_is_writable(path)


def test_governed_launch_contract_composes_real_sources_and_safe_values(
    tmp_path: Path,
) -> None:
    candidate, _reader, argv = _injected_process_fixture(tmp_path)

    contract = build_governed_supervisor_launch_contract(
        candidate,
        inherited_environment={
            "PATH": os.environ.get("PATH", ""),
            "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": os.environ[
                "BRIDGE_SIGNING_PUBLIC_KEYS_JSON"
            ],
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON": os.environ[
                "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON"
            ],
            "SECRET_TOKEN": "must-not-escape",
            "ORCH_TASK_ID": "stale-task",
            "PANTHEON_WORKTREE_ROOT": "/tmp/stale-worktree",
            "GIT_DIR": "/tmp/attacker-git-dir",
        },
    )

    assert isinstance(contract, GovernedSupervisorLaunchContract)
    assert contract.argv == argv
    assert contract.cwd == candidate.candidate_root
    assert contract.stdout_log_path == contract.stderr_log_path
    assert contract.status_command_root == candidate.candidate_root
    assert contract.status_command_runtime_sha == candidate.head_commit
    assert dict(contract.required_environment)["PYTHONDONTWRITEBYTECODE"] == "1"
    assert {source.role for source in contract.source_identities} == {
        "supervisor",
        "watchdog_intent",
        "watchdog_launcher",
        "sync_dev_root",
        "status_command_wrapper",
        "status_command",
        "command_runtime_config",
    }
    summary = promotion._governed_launch_contract_summary(contract)
    encoded = json.dumps(summary, sort_keys=True)
    assert "must-not-escape" not in encoded
    assert "stale-task" not in encoded
    assert "attacker-git-dir" not in encoded


def test_governed_launch_contract_is_read_only(tmp_path: Path) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    config_before = candidate.config_path.read_bytes()
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])
    event_before = event_log.read_bytes()
    expected_log = (
        tmp_path
        / "status-root"
        / ".orchestrator"
        / "logs"
        / f"supervisor-runtime-{candidate.head_commit}.log"
    )

    with patch.object(promotion.os, "kill") as process_signal, patch.object(
        promotion.subprocess,
        "Popen",
    ) as process_launch:
        build_governed_supervisor_launch_contract(candidate)

    process_signal.assert_not_called()
    process_launch.assert_not_called()
    assert candidate.config_path.read_bytes() == config_before
    assert event_log.read_bytes() == event_before
    assert not expected_log.exists()


def test_governed_launch_contract_rejects_missing_interpreter(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    missing = tmp_path / "missing-python"
    candidate = _replace_identity_live_config(
        candidate,
        lambda payload: payload["watchdog"]["supervisor_command"].__setitem__(
            0, str(missing)
        ),
    )

    with pytest.raises(ValueError, match="executable cannot be resolved"):
        build_governed_supervisor_launch_contract(candidate)


def test_governed_launch_contract_rejects_non_executable_interpreter(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    interpreter = tmp_path / "governed-python"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o644)
    candidate = _replace_identity_live_config(
        candidate,
        lambda payload: payload["watchdog"]["supervisor_command"].__setitem__(
            0, str(interpreter)
        ),
    )

    with pytest.raises(ValueError, match="interpreter is not executable"):
        build_governed_supervisor_launch_contract(candidate)


def test_governed_launch_contract_rejects_wrong_cwd(tmp_path: Path) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    wrong_cwd = tmp_path / "reviewer-worktree"
    wrong_cwd.mkdir()

    with pytest.raises(ValueError, match="cwd mismatch"):
        build_governed_supervisor_launch_contract(
            candidate,
            launch_cwd=wrong_cwd,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda environment: environment.pop("PANTHEON_COMMAND_RUNTIME_SHA"),
            "missing PANTHEON_COMMAND_RUNTIME_SHA",
        ),
        (
            lambda environment: environment.pop("PYTHONDONTWRITEBYTECODE"),
            "missing PYTHONDONTWRITEBYTECODE",
        ),
        (
            lambda environment: environment.__setitem__(
                "PANTHEON_COMMAND_ROOT", "/tmp/wrong-root"
            ),
            "PANTHEON_COMMAND_ROOT mismatch",
        ),
        (
            lambda environment: environment.__setitem__(
                "ORCH_WORKSPACE_PATH", "/tmp/reviewer-worktree"
            ),
            "forbidden inherited variables: ORCH_WORKSPACE_PATH",
        ),
        (
            lambda environment: environment.__setitem__(
                "PANTHEON_STATUS_COMMAND_SHA", "f" * 40
            ),
            "forbidden inherited variables: PANTHEON_STATUS_COMMAND_SHA",
        ),
        (
            lambda environment: environment.__setitem__(
                "GIT_OBJECT_DIRECTORY", "/tmp/forged-objects"
            ),
            "forbidden inherited variables: GIT_OBJECT_DIRECTORY",
        ),
    ],
)
def test_governed_launch_contract_rejects_invalid_final_environment(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    environment = build_scrubbed_launch_environment(
        candidate,
        status_root=tmp_path / "status-root",
        inherited_environment={
            "PATH": os.environ.get("PATH", ""),
            "BRIDGE_SIGNING_PUBLIC_KEYS_JSON": os.environ[
                "BRIDGE_SIGNING_PUBLIC_KEYS_JSON"
            ],
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON": os.environ[
                "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON"
            ],
        },
    )
    mutation(environment)

    with pytest.raises(ValueError, match=message):
        build_governed_supervisor_launch_contract(
            candidate,
            launch_environment=environment,
        )


def test_governed_launch_contract_rejects_unwritable_log_directory(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    log_directory = tmp_path / "status-root" / ".orchestrator" / "logs"
    filesystem = InjectedLaunchFilesystem(
        unwritable_directories={log_directory},
    )

    with pytest.raises(ValueError, match="log directory is not writable"):
        build_governed_supervisor_launch_contract(
            candidate,
            filesystem=filesystem,
        )


@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        ("runtime", "event-log directory is not writable"),
        ("worker-worktrees", "worker worktree root is not writable"),
        ("status-root/.orchestrator", "intentional-restart directory is not writable"),
    ],
)
def test_governed_launch_contract_rejects_unwritable_runtime_directories(
    tmp_path: Path,
    relative_path: str,
    message: str,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    filesystem = InjectedLaunchFilesystem(
        unwritable_directories={tmp_path / relative_path},
    )

    with pytest.raises(ValueError, match=message):
        build_governed_supervisor_launch_contract(
            candidate,
            filesystem=filesystem,
        )


def test_governed_launch_contract_rejects_unwritable_task_state_log(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = tmp_path / "runtime" / "task-state-events.jsonl"
    filesystem = InjectedLaunchFilesystem(unwritable_files={event_log})

    with pytest.raises(ValueError, match="task-state event log is not writable"):
        build_governed_supervisor_launch_contract(
            candidate,
            filesystem=filesystem,
        )


def test_governed_launch_contract_rejects_unsafe_existing_log_leaf(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    log_path = (
        tmp_path
        / "status-root"
        / ".orchestrator"
        / "logs"
        / f"supervisor-runtime-{candidate.head_commit}.log"
    )
    attacker_file = tmp_path / "attacker.log"
    attacker_file.write_text("attacker\n", encoding="utf-8")
    log_path.symlink_to(attacker_file)

    with pytest.raises(ValueError, match="symlink"):
        build_governed_supervisor_launch_contract(candidate)


def test_governed_launch_contract_rejects_unwritable_existing_log_leaf(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    log_path = (
        tmp_path
        / "status-root"
        / ".orchestrator"
        / "logs"
        / f"supervisor-runtime-{candidate.head_commit}.log"
    )
    log_path.write_text("prior launch\n", encoding="utf-8")
    filesystem = InjectedLaunchFilesystem(unwritable_files={log_path})

    with pytest.raises(ValueError, match="log is not writable"):
        build_governed_supervisor_launch_contract(
            candidate,
            filesystem=filesystem,
        )


def test_governed_launch_contract_rejects_split_stdout_stderr(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    other_log = tmp_path / "status-root" / ".orchestrator" / "logs" / "other.log"

    with pytest.raises(ValueError, match="exact durable supervisor log target"):
        build_governed_supervisor_launch_contract(
            candidate,
            stderr_log_path=other_log,
        )


def test_governed_launch_contract_rejects_missing_or_non_executable_source(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    sync_script = candidate.candidate_root / "scripts" / "sync-dev-root.sh"
    sync_script.chmod(0o644)

    with pytest.raises(ValueError, match="sync_dev_root is not executable"):
        build_governed_supervisor_launch_contract(candidate)

    sync_script.unlink()
    with pytest.raises(FileNotFoundError, match="sync_dev_root.*does not exist"):
        build_governed_supervisor_launch_contract(candidate)


def test_governed_launch_contract_rejects_unsafe_runtime_roots(
    tmp_path: Path,
) -> None:
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    candidate = _replace_identity_live_config(
        candidate,
        lambda payload: payload["worker_worktrees"].__setitem__(
            "root", str(tmp_path)
        ),
    )

    with pytest.raises(ValueError, match="cannot be a worker task worktree"):
        build_governed_supervisor_launch_contract(candidate)

    candidate, _reader, _argv = _injected_process_fixture(tmp_path / "second")
    candidate = _replace_identity_live_config(
        candidate,
        lambda payload: payload["task_state_store"].__setitem__(
            "event_log", str(candidate.candidate_root / "event-log.jsonl")
        ),
    )
    (candidate.candidate_root / "event-log.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the command runtime"):
        build_governed_supervisor_launch_contract(candidate)


def test_process_identity_binds_exact_generation_argv_cwd_git_env_and_lock(
    tmp_path: Path,
) -> None:
    candidate, reader, argv = _injected_process_fixture(tmp_path)
    identity = _discover_injected(candidate, reader)

    assert identity.generation == ProcessGeneration(
        pid=1717,
        starttime_ticks=424242,
        state="S",
    )
    assert identity.argv == argv
    summary = promotion._supervisor_process_identity_summary(identity)
    encoded_summary = json.dumps(summary, sort_keys=True)
    assert summary["argv_sha256"] == promotion._argv_sha256(argv)
    assert summary["admission_lock"]["owner_starttime_ticks"] == 424242
    assert set(summary["environment_contract"]) == {
        "BRIDGE_SIGNING_PUBLIC_KEYS_JSON",
        "PANTHEON_COMMAND_ROOT",
        "PANTHEON_COMMAND_RUNTIME_SHA",
        "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON",
        "PANTHEON_STATUS_ROOT",
        "PYTHONDONTWRITEBYTECODE",
    }
    assert "SECRET" not in encoded_summary








def test_process_identity_revalidates_candidate_inside_lock_bracket(
    tmp_path: Path,
) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    revalidator = Mock()

    discover_incumbent_supervisor_process(
        candidate,
        reader=reader,
        cwd_git_identity_reader=lambda _cwd: (
            candidate.head_commit,
            candidate.tracked_tree_identity,
        ),
        candidate_revalidator=revalidator,
    )

    revalidator.assert_called_once_with()


def test_process_identity_rejects_zero_supervisor_candidates(tmp_path: Path) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    reader.pids = ()

    with pytest.raises(ValueError, match="exactly one.*found 0"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_multiple_supervisor_candidates(
    tmp_path: Path,
) -> None:
    candidate, reader, argv = _injected_process_fixture(tmp_path)
    other = 1818
    reader.pids = (1717, other)
    reader.generations[other] = ProcessGeneration(
        pid=other,
        starttime_ticks=525252,
        state="S",
    )
    reader.argv[other] = argv

    with pytest.raises(ValueError, match="exactly one.*found 2"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_wrong_config_argv(tmp_path: Path) -> None:
    candidate, reader, argv = _injected_process_fixture(tmp_path)
    config_index = argv.index("--config") + 1
    reader.argv[1717] = (
        argv[:config_index]
        + (str(tmp_path / "wrong-config.json"),)
        + argv[config_index + 1 :]
    )

    with pytest.raises(ValueError, match="config path mismatch"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_wrong_supervisor_entrypoint(
    tmp_path: Path,
) -> None:
    candidate, reader, argv = _injected_process_fixture(tmp_path)
    reader.argv[1717] = argv[:2] + (
        str(tmp_path / "wrong" / "supervisor.py"),
    ) + argv[3:]

    with pytest.raises(ValueError, match="canonical supervisor entrypoint mismatch"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_extra_full_argv_argument(tmp_path: Path) -> None:
    candidate, reader, argv = _injected_process_fixture(tmp_path)
    reader.argv[1717] = argv + ("--once",)

    with pytest.raises(ValueError, match="full argv mismatch"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_stale_pid(tmp_path: Path) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    original = reader.generations[1717]
    reader.generation_sequences[1717] = [
        original,
        ProcessLookupError("vanished"),
    ]

    with pytest.raises(ValueError, match="enumeration was incomplete"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_reused_pid_starttime(tmp_path: Path) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    original = reader.generations[1717]
    reader.generation_sequences[1717] = [
        original,
        ProcessGeneration(pid=1717, starttime_ticks=999999, state="S"),
    ]

    with pytest.raises(ValueError, match="enumeration was incomplete"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_zombie_candidate(tmp_path: Path) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    reader.generations[1717] = ProcessGeneration(
        pid=1717,
        starttime_ticks=424242,
        state="Z",
    )

    with pytest.raises(ValueError, match="zombie"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_deleted_cwd(tmp_path: Path) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    reader.errors["read_cwd"] = ValueError("cwd is deleted")

    with pytest.raises(ValueError, match="cwd is deleted"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_wrong_cwd_realpath(tmp_path: Path) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    wrong = tmp_path / "wrong-cwd"
    wrong.mkdir()
    wrong_stat = wrong.stat()
    reader.cwd[1717] = ProcessCwdIdentity(
        path=wrong,
        device=wrong_stat.st_dev,
        inode=wrong_stat.st_ino,
    )

    with pytest.raises(ValueError, match="cwd realpath mismatch"):
        _discover_injected(candidate, reader)


@pytest.mark.parametrize(
    ("git_identity", "message"),
    [
        (("f" * 40, "b" * 40), "cwd commit mismatch"),
        (("a" * 40, "f" * 40), "cwd tree mismatch"),
    ],
)
def test_process_identity_rejects_wrong_cwd_git_identity(
    tmp_path: Path,
    git_identity: tuple[str, str],
    message: str,
) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)

    with pytest.raises(ValueError, match=message):
        _discover_injected(candidate, reader, git_identity=git_identity)


@pytest.mark.parametrize(
    "name",
    [
        "PANTHEON_COMMAND_ROOT",
        "PANTHEON_COMMAND_RUNTIME_SHA",
        "PANTHEON_STATUS_ROOT",
        "PYTHONDONTWRITEBYTECODE",
    ],
)
def test_process_identity_rejects_wrong_allowlisted_environment(
    tmp_path: Path,
    name: str,
) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    reader.environment[1717][name] = "wrong"

    with pytest.raises(ValueError, match=f"environment {name} mismatch"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_unreadable_proc_field(tmp_path: Path) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    reader.errors["read_environment_contract"] = PermissionError("denied")

    with pytest.raises(PermissionError, match="denied"):
        _discover_injected(candidate, reader)


def test_process_identity_rejects_executable_mismatch(tmp_path: Path) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    reader.executable[1717] = tmp_path / "wrong-python"

    with pytest.raises(ValueError, match="executable mismatch"):
        _discover_injected(candidate, reader)


def test_process_identity_allows_admission_lock_row_ordinal_drift(
    tmp_path: Path,
) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    original = reader.locks[0]
    reader.locks[1] = replace(original, kernel_lock_id="72")

    discovered = _discover_injected(candidate, reader)

    assert discovered.admission_lock.kernel_lock_id == "72"


def test_process_identity_rejects_admission_lock_generation_drift(
    tmp_path: Path,
) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    original = reader.locks[0]
    reader.locks[1] = replace(original, mtime_ns=34)

    with pytest.raises(ValueError, match="admission lock generation mismatch"):
        _discover_injected(candidate, reader)






def test_process_identity_rejects_admission_lock_owner_generation_mismatch(
    tmp_path: Path,
) -> None:
    candidate, reader, _argv = _injected_process_fixture(tmp_path)
    reader.locks = [
        replace(reader.locks[0], owner_starttime_ticks=313131),
    ]

    with pytest.raises(ValueError, match="owner starttime mismatch"):
        _discover_injected(candidate, reader)


def test_procfs_environment_reader_returns_only_allowlisted_contract(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    process_dir = proc_root / "1717"
    process_dir.mkdir(parents=True)
    (process_dir / "environ").write_bytes(
        b"PANTHEON_COMMAND_ROOT=/runtime\0"
        b"SECRET_TOKEN=must-not-escape\0"
        b"PANTHEON_COMMAND_RUNTIME_SHA=" + b"a" * 40 + b"\0"
        b"PANTHEON_STATUS_ROOT=/status\0"
        b"PYTHONDONTWRITEBYTECODE=1\0"
    )

    contract = ProcfsRuntimeProcessReader(
        proc_root
    ).read_environment_contract(1717)

    assert contract == {
        "PANTHEON_COMMAND_ROOT": "/runtime",
        "PANTHEON_COMMAND_RUNTIME_SHA": "a" * 40,
        "PANTHEON_STATUS_ROOT": "/status",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert "must-not-escape" not in json.dumps(contract)


def test_real_procfs_lock_capture_binds_kernel_owner_generation(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "supervisor.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        captured = ProcfsRuntimeProcessReader().read_admission_lock(lock_path)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    assert captured.owner_pid == os.getpid()
    assert captured.owner_starttime_ticks > 0
    assert captured.kernel_lock_kind == "FLOCK"
    assert captured.kernel_lock_class == "ADVISORY"
    assert captured.kernel_lock_mode == "WRITE"
    assert captured.kernel_lock_start == "0"
    assert captured.kernel_lock_end == "EOF"


def _transaction_process_identity(
    root: Path,
    generation: ProcessGeneration,
    *,
    commit: str,
    tree: str,
) -> SupervisorProcessIdentity:
    lock_path = root.parent / "status" / ".orchestrator" / "supervisor.lock"
    return SupervisorProcessIdentity(
        generation=generation,
        executable=Path(sys.executable).resolve(),
        argv=(
            str(Path(sys.executable).resolve()),
            "-u",
            "-B",
            str(root / ".orchestrator" / "supervisor.py"),
            "--config",
            str(root.parent / "live-config.json"),
            "--verbose",
        ),
        entrypoint=root / ".orchestrator" / "supervisor.py",
        config_path=root.parent / "live-config.json",
        cwd=ProcessCwdIdentity(path=root, device=11, inode=12),
        cwd_commit=commit,
        cwd_tree=tree,
        environment_contract=(
            ("PANTHEON_COMMAND_ROOT", str(root)),
            ("PANTHEON_COMMAND_RUNTIME_SHA", commit),
            ("PANTHEON_STATUS_ROOT", str(root.parent / "status")),
            ("PYTHONDONTWRITEBYTECODE", "1"),
        ),
        admission_lock=SupervisorAdmissionLockIdentity(
            path=lock_path,
            device=21,
            inode=22,
            byte_length=4,
            sha256="1" * 64,
            mtime_ns=23,
            ctime_ns=24,
            kernel_lock_id="25",
            kernel_lock_kind="FLOCK",
            kernel_lock_class="ADVISORY",
            kernel_lock_mode="WRITE",
            kernel_lock_start="0",
            kernel_lock_end="EOF",
            owner_pid=generation.pid,
            owner_starttime_ticks=generation.starttime_ticks,
        ),
    )


def _transaction_identity(root: Path, commit: str, tree: str) -> Mock:
    identity = Mock(spec=CandidateRuntimeIdentity)
    identity.candidate_root = root
    identity.head_commit = commit
    identity.tracked_tree_identity = tree
    identity.config_sha256 = "c" * 64
    identity.config_bytes = b"captured-config"
    identity.config_path = root.parent / "live-config.json"
    identity.config_device = 41
    identity.config_inode = 42
    identity.config_byte_length = len(identity.config_bytes)
    return identity


def _transaction_observation(
    process: SupervisorProcessIdentity,
    *,
    second: int,
    successful_loop: bool = True,
    invariant_failures: tuple[str, ...] = (),
    config_sha256: str = "c" * 64,
    projection_sha256: str = "projection-baseline",
    worker_queue_sha256: str = "worker-queue-baseline",
    provider_baseline_sha256: str = "provider-baseline",
) -> RuntimeObservation:
    observed = datetime(2026, 8, 2, 10, 0, second, tzinfo=timezone.utc)
    return RuntimeObservation(
        process=process,
        observed_at=observed,
        successful_loop_at=observed if successful_loop else None,
        state_sha256=f"state-{second}",
        status_sha256="status-baseline",
        provider_document_sha256="provider-document-baseline",
        provider_baseline_sha256=provider_baseline_sha256,
        projection_sha256=projection_sha256,
        worker_queue_sha256=worker_queue_sha256,
        durable_queue_sha256="durable-queue-baseline",
        config_sha256=config_sha256,
        invariant_failures=invariant_failures,
    )


class _FakePromotionBackend:
    def __init__(self, tmp_path: Path, *, fault: str | None = None) -> None:
        (tmp_path / "status" / ".orchestrator").mkdir(parents=True)
        self.fault = fault
        self.clock = 0.0
        self.wall_clock = datetime(2026, 8, 2, 10, 0, 1, tzinfo=timezone.utc)
        self.intents: list[tuple[int, str]] = []
        self.terminated: list[ProcessGeneration] = []
        self.launches: list[str] = []
        self.config_installs: list[str] = []
        self.events: list[str] = []
        self.candidate_observe_count = 0
        self.rollback_observe_count = 0
        self.task_sequence_reads = 0
        self.incumbent_generation = ProcessGeneration(100, 1000, "S")
        self.candidate_generation = ProcessGeneration(200, 2000, "S")
        self.rollback_generation = ProcessGeneration(300, 3000, "S")
        self.alive = {100: True, 200: False, 300: False}

        incumbent_root = tmp_path / ("a" * 40)
        candidate_root = tmp_path / ("b" * 40)
        self.incumbent_identity = _transaction_identity(
            incumbent_root,
            "a" * 40,
            "1" * 40,
        )
        self.candidate_identity = _transaction_identity(
            candidate_root,
            "b" * 40,
            "2" * 40,
        )
        self.incumbent_process = _transaction_process_identity(
            incumbent_root,
            self.incumbent_generation,
            commit="a" * 40,
            tree="1" * 40,
        )
        self.candidate_process = _transaction_process_identity(
            candidate_root,
            self.candidate_generation,
            commit="b" * 40,
            tree="2" * 40,
        )
        self.rollback_process = _transaction_process_identity(
            incumbent_root,
            self.rollback_generation,
            commit="a" * 40,
            tree="1" * 40,
        )
        self.candidate_contract = Mock(spec=GovernedSupervisorLaunchContract)
        self.rollback_contract = Mock(spec=GovernedSupervisorLaunchContract)
        self.candidate_config = SupervisorConfigVariant(
            command_root=candidate_root,
            supervisor_argv=self.candidate_process.argv,
            content=b"candidate-config",
            byte_length=len(b"candidate-config"),
            sha256="c" * 64,
        )
        self.rollback_config = SupervisorConfigVariant(
            command_root=incumbent_root,
            supervisor_argv=self.rollback_process.argv,
            content=b"rollback-config",
            byte_length=len(b"rollback-config"),
            sha256="c" * 64,
        )
        self.baseline = _transaction_observation(
            self.incumbent_process,
            second=0,
        )
        self.plan = PromotionPlan(
            candidate_identity=self.candidate_identity,
            candidate_config=self.candidate_config,
            candidate_launch=self.candidate_contract,
            incumbent_identity=self.incumbent_identity,
            rollback_config=self.rollback_config,
            incumbent_process=self.incumbent_process,
            rollback_launch=self.rollback_contract,
            baseline=self.baseline,
            promotion_lock_path=(
                tmp_path / "status" / ".orchestrator" / "promotion.lock"
            ),
            runtime_admission_lock_path=(
                tmp_path / "status" / ".orchestrator" / "runtime-admission.lock"
            ),
            task_state_lock_path=(
                tmp_path / "status" / ".orchestrator" / "task-state.lock"
            ),
        )

    def promotion_lock_path(self, candidate_root: Path) -> Path:
        assert candidate_root == self.candidate_identity.candidate_root
        return self.plan.promotion_lock_path

    def prepare(self, candidate_root: Path) -> PromotionPlan:
        assert candidate_root == self.candidate_identity.candidate_root
        self.events.append("prepare")
        return self.plan

    def revalidate(self, plan: PromotionPlan) -> RuntimeObservation:
        assert plan is self.plan
        self.events.append("revalidate")
        if self.fault == "runtime_document_churn":
            return replace(
                self.baseline,
                state_sha256="state-advanced-under-lock",
                status_sha256="status-advanced-under-lock",
                provider_document_sha256="provider-advanced-under-lock",
            )
        if self.fault == "admission_identity_drift":
            return replace(self.baseline, config_sha256="config-changed-under-lock")
        if self.fault == "admission_health_failure":
            return replace(
                self.baseline,
                invariant_failures=("provider_readiness_baseline",),
            )
        return self.baseline

    def migrate_task_state_store(self, plan: PromotionPlan) -> dict[str, Any]:
        assert plan.candidate_identity is self.candidate_identity
        self.events.append("migrate")
        if self.fault == "migration_failure":
            raise ValueError("migration failure")
        return {"ok": True, "projection_parity": True}

    def finalize_candidate_launch(
        self, plan: PromotionPlan
    ) -> GovernedSupervisorLaunchContract:
        self.events.append("finalize-launch")
        if self.fault == "migration_failure":
            raise AssertionError("launch must not finalize after migration failure")
        self.candidate_contract.task_state_event_log_identity = Mock()
        return self.candidate_contract

    def task_state_store_sequence(self, plan: PromotionPlan) -> int:
        self.events.append("task-sequence")
        self.task_sequence_reads += 1
        if self.fault == "v2_mutation_during_rollback_lock":
            return 1 if self.task_sequence_reads == 1 else 2
        return 2 if self.fault == "v2_mutation_then_failure" else 1

    def runtime_execution_authority_digests(
        self,
        identity: CandidateRuntimeIdentity,
    ) -> tuple[str, str]:
        assert identity is self.candidate_identity
        self.events.append("runtime-authority")
        if self.fault == "candidate_runtime_authority_drift":
            return "candidate-worker-queue", "candidate-durable-queue"
        return (
            self.baseline.worker_queue_sha256,
            self.baseline.durable_queue_sha256,
        )

    def observe(
        self,
        identity: CandidateRuntimeIdentity,
        contract: GovernedSupervisorLaunchContract,
        generation: ProcessGeneration,
        *,
        require_current_dev_identity: bool,
    ) -> RuntimeObservation:
        if identity is self.candidate_identity:
            assert require_current_dev_identity is True
            assert contract is self.candidate_contract
            assert generation == self.candidate_generation
            self.candidate_observe_count += 1
            if self.fault in {
                "wrong_candidate_cwd",
                "wrong_candidate_commit",
                "wrong_candidate_tree",
                "v2_mutation_during_rollback_lock",
            }:
                raise ValueError(self.fault)
            if self.fault == "missing_heartbeat":
                return _transaction_observation(
                    self.candidate_process,
                    second=1,
                    successful_loop=False,
                )
            failure_map = {
                "projection_mismatch": "task_state_shadow_valid",
                "lease_mismatch": "worker_lease_parity_and_no_duplicates",
                "duplicate_worker": "worker_lease_parity_and_no_duplicates",
                "provider_not_ready": "provider_readiness_baseline",
            }
            if self.fault in failure_map:
                return _transaction_observation(
                    self.candidate_process,
                    second=1,
                    invariant_failures=(failure_map[self.fault],),
                )
            if self.fault == "config_drift":
                return _transaction_observation(
                    self.candidate_process,
                    second=1,
                    config_sha256="d" * 64,
                )
            candidate_marker_sequences = {
                "candidate_stale_loop": (0,),
                "candidate_equal_boundary_loop": (1,),
                "candidate_regressing_loop": (3, 2),
                "candidate_out_of_order_loop": (2, 4, 3),
            }
            if self.fault in candidate_marker_sequences:
                markers = candidate_marker_sequences[self.fault]
                marker = markers[min(self.candidate_observe_count - 1, len(markers) - 1)]
                return _transaction_observation(
                    self.candidate_process,
                    second=marker,
                )
            if self.fault in {
                "rollback_config_install_failure",
                "rollback_launch_failure",
                "rollback_config_drift",
                "rollback_projection_drift",
                "rollback_worker_drift",
                "rollback_provider_drift",
                "rollback_stale_loop",
                "rollback_equal_boundary_loop",
                "rollback_regressing_loop",
                "rollback_out_of_order_loop",
                "v2_mutation_then_failure",
                "candidate_runtime_authority_drift",
            }:
                raise ValueError("candidate_postcheck_failure")
            return _transaction_observation(
                self.candidate_process,
                second=1 + min(self.candidate_observe_count, 3),
            )

        assert identity is self.incumbent_identity
        assert require_current_dev_identity is False
        assert contract is self.rollback_contract
        assert generation == self.rollback_generation
        self.rollback_observe_count += 1
        rollback_marker_sequences = {
            "rollback_stale_loop": (0,),
            "rollback_equal_boundary_loop": (1,),
            "rollback_regressing_loop": (6, 5),
            "rollback_out_of_order_loop": (5, 7, 6),
        }
        markers = rollback_marker_sequences.get(self.fault)
        marker = (
            markers[min(self.rollback_observe_count - 1, len(markers) - 1)]
            if markers is not None
            else 4 + min(self.rollback_observe_count, 3)
        )
        return _transaction_observation(
            self.rollback_process,
            second=marker,
            config_sha256=(
                "e" * 64 if self.fault == "rollback_config_drift" else "c" * 64
            ),
            projection_sha256=(
                "projection-drift"
                if self.fault == "rollback_projection_drift"
                else "projection-baseline"
            ),
            worker_queue_sha256=(
                "worker-queue-drift"
                if self.fault == "rollback_worker_drift"
                else "worker-queue-baseline"
            ),
            provider_baseline_sha256=(
                "provider-drift"
                if self.fault == "rollback_provider_drift"
                else "provider-baseline"
            ),
        )

    def record_intent(
        self,
        identity: CandidateRuntimeIdentity,
        *,
        old_pid: int,
        target_sha: str,
    ) -> None:
        assert identity in {self.candidate_identity, self.incumbent_identity}
        self.events.append(f"intent:{target_sha[0]}")
        self.intents.append((old_pid, target_sha))

    def install_config(
        self,
        identity: CandidateRuntimeIdentity,
        variant: SupervisorConfigVariant,
        *,
        allowed_predecessors: dict[str, bytes],
    ) -> CandidateRuntimeIdentity:
        assert allowed_predecessors
        if variant is self.candidate_config:
            self.events.append("install:candidate")
            self.config_installs.append("candidate")
            if self.fault == "candidate_config_install_failure":
                raise OSError("candidate config install failure")
            return self.candidate_identity
        assert variant is self.rollback_config
        self.events.append("install:rollback")
        self.config_installs.append("rollback")
        if self.fault == "rollback_config_install_failure":
            raise OSError("rollback config install failure")
        return self.incumbent_identity

    def launch(
        self,
        identity: CandidateRuntimeIdentity,
        contract: GovernedSupervisorLaunchContract,
        *,
        require_current_dev_identity: bool,
    ) -> ProcessGeneration:
        if identity is self.candidate_identity:
            assert require_current_dev_identity is True
            self.launches.append("candidate")
            self.events.append("launch:candidate")
            if self.fault == "candidate_launch_failure":
                raise ProcessLaunchError("candidate launch failure")
            if self.fault == "candidate_identity_unknown_live":
                self.alive[201] = True
                raise ProcessLaunchError(
                    "candidate generation capture failure",
                    pid=201,
                    child_absence_proven=False,
                )
            self.alive[200] = True
            return self.candidate_generation
        assert identity is self.incumbent_identity
        assert require_current_dev_identity is False
        self.launches.append("rollback")
        self.events.append("launch:rollback")
        if self.fault == "rollback_launch_failure":
            raise ProcessLaunchError(
                "rollback launch failure",
                pid=301,
                child_absence_proven=True,
            )
        self.alive[300] = True
        return self.rollback_generation

    def terminate(self, generation: ProcessGeneration, *, timeout: float) -> None:
        assert timeout > 0
        self.events.append(f"terminate:{generation.pid}")
        self.terminated.append(generation)
        self.alive[generation.pid] = False

    def generation_is_alive(self, generation: ProcessGeneration) -> bool:
        return self.alive.get(generation.pid, False)

    def pid_is_absent(self, pid: int) -> bool:
        return not self.alive.get(pid, False)

    def utcnow(self) -> datetime:
        return self.wall_clock

    def monotonic(self) -> float:
        self.clock += 0.001
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.clock += seconds


def _run_fake_transaction(
    tmp_path: Path,
    *,
    fault: str | None = None,
) -> tuple[dict[str, Any], _FakePromotionBackend, Path]:
    backend = _FakePromotionBackend(tmp_path, fault=fault)
    evidence_path = tmp_path / "evidence" / "transaction.json"
    transaction = PromotionTransaction(
        evidence_path=evidence_path,
        backend=backend,
        postcheck_timeout=0.04,
        poll_interval=0.005,
        lock_timeout=1.0,
        termination_timeout=1.0,
    )
    with patch("promote_supervisor_runtime.os.kill") as kill:
        result = transaction.run(backend.candidate_identity.candidate_root)
    kill.assert_not_called()
    return result, backend, evidence_path


def test_transaction_promotes_only_after_three_distinct_candidate_loops(
    tmp_path: Path,
) -> None:
    result, backend, evidence_path = _run_fake_transaction(tmp_path)

    assert result["outcome"] == "promoted"
    assert result["exit_code"] == 0
    assert result["state"] == PromotionState.PROMOTED.value
    assert len(result["candidate_observations"]) == 3
    assert backend.intents == [(100, "b" * 40)]
    assert backend.terminated == [backend.incumbent_generation]
    assert backend.events.index("migrate") < backend.events.index("intent:b")
    assert backend.events.index("finalize-launch") < backend.events.index("terminate:100")
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == result


def test_migration_failure_aborts_before_intent_term_or_config_install(
    tmp_path: Path,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(
        tmp_path, fault="migration_failure"
    )

    assert result["outcome"] == "aborted"
    assert result["state"] == PromotionState.ABORTED.value
    assert backend.intents == []
    assert backend.terminated == []
    assert backend.config_installs == []
    assert backend.launches == []


def test_post_genesis_v2_mutation_forbids_silent_v1_rollback(
    tmp_path: Path,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(
        tmp_path, fault="v2_mutation_then_failure"
    )

    assert result["outcome"] == "forward_recovery_required"
    assert result["state"] == PromotionState.FORWARD_RECOVERY_REQUIRED.value
    assert result["exit_code"] == 4
    assert backend.config_installs == ["candidate"]
    assert backend.launches == ["candidate"]
    assert backend.alive[200] is True
    assert "install:rollback" not in backend.events


def test_v2_mutation_between_fast_precheck_and_rollback_lock_forces_forward_recovery(
    tmp_path: Path,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(
        tmp_path,
        fault="v2_mutation_during_rollback_lock",
    )

    assert result["outcome"] == "forward_recovery_required"
    assert result["state"] == PromotionState.FORWARD_RECOVERY_REQUIRED.value
    assert result["exit_code"] == 4
    assert backend.task_sequence_reads == 2
    assert backend.config_installs == ["candidate"]
    assert backend.terminated == [backend.incumbent_generation]
    assert backend.alive[backend.candidate_generation.pid] is True
    assert "install:rollback" not in backend.events


def test_candidate_queue_or_worker_authority_forbids_v1_rollback(
    tmp_path: Path,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(
        tmp_path,
        fault="candidate_runtime_authority_drift",
    )

    assert result["outcome"] == "forward_recovery_required"
    assert result["state"] == PromotionState.FORWARD_RECOVERY_REQUIRED.value
    assert result["exit_code"] == 4
    assert result["history"][-1]["details"]["runtime_authority_changed"] is True
    assert backend.config_installs == ["candidate"]
    assert backend.launches == ["candidate"]
    assert backend.alive[backend.candidate_generation.pid] is True
    assert "runtime-authority" in backend.events
    assert "install:rollback" not in backend.events








@pytest.mark.parametrize(
    "fault,expected_fragment",
    [
        ("candidate_config_install_failure", "candidate config install failure"),
        ("candidate_launch_failure", "candidate launch failure"),
        ("missing_heartbeat", "last_successful_loop_at"),
        ("wrong_candidate_cwd", "wrong_candidate_cwd"),
        ("wrong_candidate_commit", "wrong_candidate_commit"),
        ("wrong_candidate_tree", "wrong_candidate_tree"),
        ("projection_mismatch", "task_state_shadow_valid"),
        ("lease_mismatch", "worker_lease_parity_and_no_duplicates"),
        ("duplicate_worker", "worker_lease_parity_and_no_duplicates"),
        ("provider_not_ready", "provider_readiness_baseline"),
        ("config_drift", "config bytes drifted"),
    ],
)
def test_candidate_failure_matrix_rolls_back_to_new_verified_pid(
    tmp_path: Path,
    fault: str,
    expected_fragment: str,
) -> None:
    result, backend, evidence_path = _run_fake_transaction(tmp_path, fault=fault)

    assert result["outcome"] == "rolled_back"
    assert result["exit_code"] != 0
    assert result["rollback_pid"] == 300
    assert result["rollback_pid"] != result["incumbent"]["pid"]
    assert len(result["rollback_observations"]) == 3
    assert backend.config_installs == ["candidate", "rollback"]
    assert expected_fragment in result["original_failure"]
    if fault in {"candidate_config_install_failure", "candidate_launch_failure"}:
        assert result["candidate_pid"] is None
        if fault == "candidate_launch_failure":
            assert result["candidate_child_absence_proven"] is True
        else:
            assert backend.intents == [(100, "b" * 40)]
    else:
        assert backend.intents[-1] == (200, "a" * 40)
        assert backend.terminated[-1] == backend.candidate_generation
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["outcome"] == "rolled_back"


@pytest.mark.parametrize(
    "fault,rollback_fragment",
    [
        ("rollback_config_install_failure", "rollback config install failure"),
        ("rollback_launch_failure", "rollback launch failure"),
        ("rollback_config_drift", "config bytes drifted"),
        ("rollback_projection_drift", "Rollback baseline mismatch: projection"),
        (
            "rollback_worker_drift",
            "Rollback baseline mismatch: worker_queue_lease_parity",
        ),
        ("rollback_provider_drift", "Rollback baseline mismatch: provider_baseline"),
    ],
)
def test_rollback_failure_is_nonzero_and_records_both_failures(
    tmp_path: Path,
    fault: str,
    rollback_fragment: str,
) -> None:
    result, _backend, evidence_path = _run_fake_transaction(tmp_path, fault=fault)

    assert result["outcome"] == "rollback_failed"
    assert result["exit_code"] != 0
    assert "candidate_postcheck_failure" in result["original_failure"]
    assert rollback_fragment in result["rollback_failure"]
    assert result["candidate"]["root"]
    assert result["incumbent"]["root"]
    assert result["candidate"]["commit"] == "b" * 40
    assert result["incumbent"]["commit"] == "a" * 40
    assert result["candidate"]["tree"] == "2" * 40
    assert result["incumbent"]["tree"] == "1" * 40
    assert result["candidate"]["config_sha256"] == "c" * 64
    assert _backend.config_installs == ["candidate", "rollback"]
    assert json.loads(evidence_path.read_text(encoding="utf-8"))["state"] == "rollback_failed"


@pytest.mark.parametrize(
    "fault",
    [
        "candidate_stale_loop",
        "candidate_equal_boundary_loop",
        "candidate_regressing_loop",
        "candidate_out_of_order_loop",
    ],
)
def test_candidate_loop_markers_must_be_post_launch_and_strictly_increasing(
    tmp_path: Path,
    fault: str,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(tmp_path, fault=fault)

    assert result["outcome"] == "rolled_back"
    assert result["candidate_launch_boundary_at"] == "2026-08-02T10:00:01Z"
    assert result["rollback_launch_boundary_at"] == "2026-08-02T10:00:01Z"
    assert backend.launches == ["candidate", "rollback"]
    assert len(result["rollback_observations"]) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "rollback_stale_loop",
        "rollback_equal_boundary_loop",
        "rollback_regressing_loop",
        "rollback_out_of_order_loop",
    ],
)
def test_rollback_loop_markers_must_be_post_launch_and_strictly_increasing(
    tmp_path: Path,
    fault: str,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(tmp_path, fault=fault)

    assert result["outcome"] == "rollback_failed"
    assert result["exit_code"] != 0
    assert result["rollback_launch_boundary_at"] == "2026-08-02T10:00:01Z"
    assert backend.launches == ["candidate", "rollback"]
    assert (
        "launch boundary" in result["rollback_failure"]
        or "marker regressed" in result["rollback_failure"]
    )


def test_unknown_live_candidate_blocks_rollback_launch_and_is_durable(
    tmp_path: Path,
) -> None:
    result, backend, evidence_path = _run_fake_transaction(
        tmp_path,
        fault="candidate_identity_unknown_live",
    )

    assert result["outcome"] == "rollback_failed"
    assert result["candidate_pid"] == 201
    assert result["candidate_child_absence_proven"] is False
    assert result["rollback_pid"] is None
    assert result["rollback_launch_boundary_at"] is None
    assert "unknown generation" in result["rollback_failure"]
    assert "rollback launch is prohibited" in result["rollback_failure"]
    assert backend.alive[201] is True
    assert backend.launches == ["candidate"]
    assert backend.config_installs == ["candidate"]
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["outcome"] == "rollback_failed"
    assert persisted["candidate_pid"] == 201
    assert persisted["candidate_child_absence_proven"] is False


def _exercise_os_launch_generation_failure(
    tmp_path: Path,
    process: Mock,
) -> ProcessLaunchError:
    identity = Mock(spec=CandidateRuntimeIdentity)
    contract = Mock(spec=GovernedSupervisorLaunchContract)
    contract.argv = (str(Path(sys.executable).resolve()), "supervisor.py")
    contract.cwd = tmp_path
    contract.status_root = tmp_path / "status"
    contract.required_environment = ()
    contract.stdout_log_path = tmp_path / "supervisor.log"
    process.pid = 4321
    reader = Mock(spec=ProcfsRuntimeProcessReader)
    reader.read_generation.side_effect = ValueError("injected procfs failure")
    backend = promotion.OSPromotionBackend(reader=reader)

    with patch(
        "promote_supervisor_runtime.build_governed_supervisor_launch_contract",
        return_value=contract,
    ), patch(
        "promote_supervisor_runtime.build_scrubbed_launch_environment",
        return_value={},
    ), patch(
        "promote_supervisor_runtime._validate_governed_launch_environment",
    ), patch(
        "promote_supervisor_runtime.subprocess.Popen",
        return_value=process,
    ):
        with pytest.raises(ProcessLaunchError) as captured:
            backend.launch(
                identity,
                contract,
                require_current_dev_identity=True,
            )
    return captured.value


def test_os_launch_contains_and_reaps_child_when_generation_capture_fails(
    tmp_path: Path,
) -> None:
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0

    error = _exercise_os_launch_generation_failure(tmp_path, process)

    assert error.pid == 4321
    assert error.generation is None
    assert error.child_absence_proven is True
    assert "terminated and reaped" in str(error)
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5.0)
    process.kill.assert_not_called()


def test_os_launch_reports_unknown_live_child_when_containment_cannot_prove_absence(
    tmp_path: Path,
) -> None:
    process = Mock()
    process.poll.return_value = None
    process.terminate.side_effect = RuntimeError("terminate unavailable")
    process.kill.side_effect = RuntimeError("kill unavailable")
    process.wait.side_effect = [
        subprocess.TimeoutExpired("supervisor", 5.0),
        subprocess.TimeoutExpired("supervisor", 5.0),
    ]

    error = _exercise_os_launch_generation_failure(tmp_path, process)

    assert error.pid == 4321
    assert error.generation is None
    assert error.child_absence_proven is False
    assert "containment could not be proven" in str(error)
    assert "terminate:RuntimeError:terminate unavailable" in error.cleanup_error
    assert "kill:RuntimeError:kill unavailable" in error.cleanup_error


def test_runtime_document_churn_under_admission_lock_revalidates_before_termination(
    tmp_path: Path,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(
        tmp_path,
        fault="runtime_document_churn",
    )

    assert result["outcome"] == "promoted"
    assert backend.intents == [(100, "b" * 40)]
    assert backend.terminated == [backend.incumbent_generation]


def test_process_or_config_identity_drift_under_admission_lock_aborts_without_termination(
    tmp_path: Path,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(
        tmp_path,
        fault="admission_identity_drift",
    )

    assert result["outcome"] == "aborted"
    assert "process/config identity changed before TERM" in result["original_failure"]
    assert backend.intents == []
    assert backend.terminated == []


def test_admission_health_failure_aborts_without_termination(
    tmp_path: Path,
) -> None:
    result, backend, _evidence_path = _run_fake_transaction(
        tmp_path,
        fault="admission_health_failure",
    )

    assert result["outcome"] == "aborted"
    assert "health invariants failed before TERM" in result["original_failure"]
    assert "provider_readiness_baseline" in result["original_failure"]
    assert backend.intents == []
    assert backend.terminated == []


def test_runtime_admission_lock_is_same_owner_reentrant(tmp_path: Path) -> None:
    lock = RuntimeAdmissionLock(tmp_path / "runtime-admission.lock", timeout=1.0)

    with lock.held():
        assert lock.depth == 1
        with lock.held():
            assert lock.depth == 2
        assert lock.depth == 1

    assert lock.depth == 0


def test_runtime_admission_lock_preserves_bounded_external_contention(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "runtime-admission.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        lock = RuntimeAdmissionLock(lock_path, timeout=0.01)

        with pytest.raises(TimeoutError, match="Timed out acquiring"):
            lock.acquire()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_promotion_lock_preserves_bounded_external_contention(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "promotion.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        lock = PromotionLock(lock_path, timeout=0.01)

        with pytest.raises(TimeoutError, match="Timed out acquiring promotion"):
            lock.acquire()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_promotion_lock_rejects_symlink_leaf(tmp_path: Path) -> None:
    target = tmp_path / "target.lock"
    target.touch()
    alias = tmp_path / "promotion.lock"
    alias.symlink_to(target)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        PromotionLock(alias, timeout=1.0).acquire()


def test_runtime_admission_lock_composes_with_watchdog_intent_writer(
    tmp_path: Path,
) -> None:
    status_root = tmp_path / "status"
    state_path = status_root / ".orchestrator" / "state.json"
    status_path = status_root / "ai-status.json"
    write_json(state_path, {})
    config = {
        "paths": {
            "state_file": str(state_path),
            "status_file": str(status_path),
        }
    }
    lock_path = status_root / ".orchestrator" / "runtime-admission.lock"
    identity = Mock(spec=CandidateRuntimeIdentity)
    backend = promotion.OSPromotionBackend(reader=Mock())

    with patch.object(promotion, "_strict_live_config", return_value=config):
        with RuntimeAdmissionLock(lock_path, timeout=1.0).held():
            backend.record_intent(
                identity,
                old_pid=4321,
                target_sha="a" * 40,
            )

    intent_path = status_root / ".orchestrator" / "supervisor-restart-intent.json"
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    assert intent["kind"] == "intentional_deploy_restart"
    assert intent["old_pid"] == 4321
    assert intent["target_sha"] == "a" * 40


def test_os_backend_termination_signals_only_captured_generation(
    tmp_path: Path,
) -> None:
    _identity, reader, _argv = _injected_process_fixture(tmp_path)
    generation = reader.generations[1717]
    reader.generation_sequences[1717] = [
        generation,
        ProcessLookupError(1717),
    ]
    backend = promotion.OSPromotionBackend(reader=reader)

    with patch("promote_supervisor_runtime.os.kill") as kill:
        backend.terminate(generation, timeout=1.0)

    kill.assert_called_once_with(generation.pid, promotion.signal.SIGTERM)


def test_os_backend_termination_never_signals_reused_pid(tmp_path: Path) -> None:
    _identity, reader, _argv = _injected_process_fixture(tmp_path)
    captured = reader.generations[1717]
    reader.generations[1717] = replace(captured, starttime_ticks=999999)
    backend = promotion.OSPromotionBackend(reader=reader)

    with patch("promote_supervisor_runtime.os.kill") as kill:
        backend.terminate(captured, timeout=1.0)

    kill.assert_not_called()






























def test_generated_logs_use_dedicated_directory_allowlist() -> None:
    assert promotion.PurePosixPath(".orchestrator/logs") in promotion.ALLOWED_GENERATED_UNTRACKED_DIRECTORIES
































def test_os_launch_filesystem_capture_regular_file_rejects_hard_link(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.txt"
    original.write_text("hello", encoding="utf-8")
    hard_link = tmp_path / "hard_link.txt"
    os.link(original, hard_link)

    fs = promotion.OSLaunchFilesystem()
    with pytest.raises(ValueError, match="must not be hard-linked"):
        fs.capture_regular_file(
            hard_link,
            role="test_hard_link",
            require_executable=False,
        )























































def test_worker_lease_parity_ignores_unstarted_queued_or_pending_events() -> None:
    """Queued and pending events do not have lease_owner yet and should not trigger missing_lease_owner."""
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    ai_status = {"tasks": []}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {},
        "queue": {
            "events": {
                "evt-queued-1": {"status": "queued", "task_id": "T1"},
                "evt-pending-2": {"status": "pending", "task_id": "T2"},
            }
        },
        "worker_worktrees": {"leases": {}},
    }

    with patch("promote_supervisor_runtime.pid_is_alive", return_value=True), patch("promote_supervisor_runtime.lock_held", return_value=True):
        invariants = evaluate_promotion_invariants(
            health_report=health_report,
            ai_status=ai_status,
            state=state,
            lock_path=Path("/tmp/fake.lock"),
        )

    worker_inv = next(i for i in invariants if i["name"] == "worker_lease_parity_and_no_duplicates")
    assert worker_inv["ok"] is True
    assert len(worker_inv["details"]["reasons"]) == 0


def test_worker_lease_parity_rejects_started_event_missing_lease_owner() -> None:
    """A started queue event without a lease_owner must fail-closed with missing_lease_owner."""
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    ai_status = {"tasks": []}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {},
        "queue": {
            "events": {
                "evt-started-1": {"status": "started", "task_id": "T1"},
            }
        },
        "worker_worktrees": {"leases": {}},
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
    assert "missing_lease_owner:evt-started-1" in worker_inv["details"]["reasons"]


def test_worker_lease_parity_coherent_started_event_passes() -> None:
    """A fully coherent started queue event with worker and lease passes invariant checks."""
    health_report = {"healthy": True, "supervisor": {"lifecycle": "running", "pid": 100}}
    ai_status = {"tasks": []}
    state = {
        "supervisor": {"lifecycle": "running"},
        "workers": {
            "w1": {
                "status": "running",
                "current_task_id": "T1",
                "run_id": "w1-run",
                "queue_event_id": "evt-1",
            }
        },
        "queue": {
            "events": {
                "evt-1": {
                    "status": "started",
                    "task_id": "T1",
                    "run_id": "w1-run",
                    "lease_owner": "w1-run",
                }
            }
        },
        "worker_worktrees": {
            "leases": {
                "l1": {"task_id": "T1", "run_id": "w1-run", "queue_event_id": "evt-1"}
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
    assert worker_inv["ok"] is True
    assert len(worker_inv["details"]["reasons"]) == 0


# ---------------------------------------------------------------------------
# SUP-RUNTIME-V10-APPEND-ONLY-EVENT-LOG-CAPTURE-20260811 Adversarial Tests
# ---------------------------------------------------------------------------

def test_task_state_event_log_concurrent_append_during_capture(tmp_path: Path) -> None:
    """Prove concurrent append growth during journal capture succeeds without changing identity baseline."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])

    # Build initial contract
    contract = build_governed_supervisor_launch_contract(candidate)
    assert contract.task_state_event_log_identity is not None
    initial_identity = contract.task_state_event_log_identity
    initial_size = initial_identity.captured_size

    # Concurrently append bytes to the live journal
    with event_log.open("a") as f:
        f.write('{"event": "concurrent_append_test", "ts": "2026-08-11T02:00:00Z"}\n')

    # Re-capture / build contract with baseline identity
    contract_after = build_governed_supervisor_launch_contract(
        candidate,
        baseline_task_state_event_log_identity=initial_identity,
    )
    assert contract_after.task_state_event_log_identity is not None
    assert contract_after.task_state_event_log_identity == initial_identity
    assert contract_after.task_state_event_log_identity.captured_size == initial_size
    assert contract_after.task_state_event_log_identity.captured_at_size > initial_size
    assert contract_after == contract


def test_task_state_event_log_reproduction_transaction_f031441_concurrent_append(tmp_path: Path) -> None:
    """Reproduce transaction f031441af607e8bd9631e4b08b34315a417209f9223c515ff8fb40aeb452ee9e:

    The 8.23 GB append-only event log receives concurrent appends during promotion preflight/revalidation.
    With descriptor-bound append-only journal capture, promotion does NOT fail with 'changed during capture'.
    """
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])

    # Simulate active concurrent appends by monkey-patching _hash_descriptor_range to write appends mid-capture
    original_hash = promotion._hash_descriptor_range
    appended = False

    def hashing_with_concurrent_append(descriptor: int, *, length: int) -> str:
        nonlocal appended
        res = original_hash(descriptor, length=length)
        if not appended:
            appended = True
            with event_log.open("a") as f:
                f.write('{"event": "live_append_during_hash"}\n')
        return res

    with patch.object(promotion, "_hash_descriptor_range", side_effect=hashing_with_concurrent_append):
        contract = build_governed_supervisor_launch_contract(candidate)

    assert contract.task_state_event_log_identity is not None
    assert contract.task_state_event_log_identity.captured_at_size > contract.task_state_event_log_identity.captured_size


def test_task_state_event_log_rejects_prefix_overwrite(tmp_path: Path) -> None:
    """Overwriting bytes in the journal prefix must abort promotion preflight."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])
    event_log.write_bytes(b"initial_prefix_data_12345\n")

    contract = build_governed_supervisor_launch_contract(candidate)
    baseline = contract.task_state_event_log_identity

    # Overwrite prefix byte at offset 2
    with event_log.open("r+b") as f:
        f.seek(2)
        f.write(b"X")

    with pytest.raises(ValueError, match="prefix changed"):
        build_governed_supervisor_launch_contract(
            candidate,
            baseline_task_state_event_log_identity=baseline,
        )


def test_task_state_event_log_rejects_same_size_rewrite(tmp_path: Path) -> None:
    """Rewriting journal content without changing file size must abort promotion."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])
    event_log.write_bytes(b"abcdefghijklmnopqrstuvwxyz\n")

    contract = build_governed_supervisor_launch_contract(candidate)
    baseline = contract.task_state_event_log_identity

    # Replace bytes with same length
    with event_log.open("r+b") as f:
        f.seek(5)
        f.write(b"12345")

    with pytest.raises(ValueError, match="prefix changed"):
        build_governed_supervisor_launch_contract(
            candidate,
            baseline_task_state_event_log_identity=baseline,
        )


def test_task_state_event_log_rejects_truncation(tmp_path: Path) -> None:
    """Truncating journal size below baseline must abort promotion."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])
    event_log.write_bytes(b"line1\nline2\nline3\nline4\n")

    contract = build_governed_supervisor_launch_contract(candidate)
    baseline = contract.task_state_event_log_identity

    # Truncate file
    with event_log.open("r+b") as f:
        f.truncate(5)

    with pytest.raises(ValueError, match="truncated below baseline size"):
        build_governed_supervisor_launch_contract(
            candidate,
            baseline_task_state_event_log_identity=baseline,
        )


def test_task_state_event_log_rejects_truncate_and_regrow(tmp_path: Path) -> None:
    """Truncating and regrowing journal content must abort promotion."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])
    event_log.write_bytes(b"original_journal_content_block_1\n")

    contract = build_governed_supervisor_launch_contract(candidate)
    baseline = contract.task_state_event_log_identity

    # Truncate and rewrite new content to same or larger size
    event_log.write_bytes(b"completely_new_journal_content_2\n")

    with pytest.raises(ValueError, match="prefix changed"):
        build_governed_supervisor_launch_contract(
            candidate,
            baseline_task_state_event_log_identity=baseline,
        )


def test_task_state_event_log_rejects_inode_swap(tmp_path: Path) -> None:
    """Replacing the journal file with a new file (different inode) must abort promotion."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])

    contract = build_governed_supervisor_launch_contract(candidate)
    baseline = contract.task_state_event_log_identity

    # Create replacement file first so OS assigns a distinct inode, then atomic replace
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(b"new_file_different_inode\n")
    os.replace(replacement, event_log)

    with pytest.raises(ValueError, match="inode changed"):
        build_governed_supervisor_launch_contract(
            candidate,
            baseline_task_state_event_log_identity=baseline,
        )


def test_task_state_event_log_rejects_symlink(tmp_path: Path) -> None:
    """Replacing the journal file with a symlink must abort promotion."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])
    target = tmp_path / "symlink-target.jsonl"
    target.write_bytes(b"target\n")

    event_log.unlink()
    event_log.symlink_to(target)

    with pytest.raises(ValueError, match="symlink|non-directory component"):
        build_governed_supervisor_launch_contract(candidate)


def test_task_state_event_log_rejects_hard_link(tmp_path: Path) -> None:
    """Hard-linked journal file must abort promotion."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])
    link = tmp_path / "hard-link.jsonl"
    link.hardlink_to(event_log)

    with pytest.raises(ValueError, match="must not be hard-linked"):
        build_governed_supervisor_launch_contract(candidate)


def test_task_state_event_log_rejects_mode_drift(tmp_path: Path) -> None:
    """Mode permission drift on journal file must abort promotion."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])

    contract = build_governed_supervisor_launch_contract(candidate)
    baseline = contract.task_state_event_log_identity

    event_log.chmod(0o777)

    with pytest.raises(ValueError, match="mode changed"):
        build_governed_supervisor_launch_contract(
            candidate,
            baseline_task_state_event_log_identity=baseline,
        )


def test_task_state_event_log_handles_empty_and_large_journals(tmp_path: Path) -> None:
    """Empty journals (0 bytes) and large journals behave cleanly under append capture."""
    candidate, _reader, _argv = _injected_process_fixture(tmp_path)
    event_log = Path(json.loads(candidate.config_bytes)["task_state_store"]["event_log"])
    event_log.write_bytes(b"")

    # Empty journal
    contract1 = build_governed_supervisor_launch_contract(candidate)
    assert contract1.task_state_event_log_identity is not None
    assert contract1.task_state_event_log_identity.captured_size == 0

    # Append to empty journal
    event_log.write_bytes(b"x" * 200000)  # 200 KB
    contract2 = build_governed_supervisor_launch_contract(
        candidate,
        baseline_task_state_event_log_identity=contract1.task_state_event_log_identity,
    )
    assert contract2.task_state_event_log_identity == contract1.task_state_event_log_identity
    assert contract2.task_state_event_log_identity.captured_at_size == 200000
