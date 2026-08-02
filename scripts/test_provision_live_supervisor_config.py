from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import provision_live_supervisor_config as provision

from provision_live_supervisor_config import (
    apply_coordination_policy,
    apply_provider_account_schema,
    apply_ready_dispatcher_policy,
    apply_supervisor_lease_policy,
    apply_task_state_store,
    build_live_config,
    canonical_status_paths,
    canonical_watchdog_runtime_paths,
    ensure_approval_queue_marker,
    main,
    validate_approval_queue_marker,
    validated_immutable_command_root,
    write_json_atomic,
)


def test_apply_provider_account_schema_removes_stale_live_aliases() -> None:
    repo = {
        "ready_dispatcher": {
            "require_explicit_provider_accounts": True,
            "allow_legacy_provider_account_aliases": False,
            "max_concurrent_per_account": {"shared": 1},
        },
        "providers": {
            "claude": {"account": "shared"},
            "claude2": {"account": "shared"},
        },
    }
    rendered = {
        "ready_dispatcher": {
            "max_concurrent_per_quota_group": {"claude": 1},
        },
        "providers": {
            "claude": {"account_group": "shared"},
            "claude2": {"quota_group": "claude2"},
        },
    }

    apply_provider_account_schema(repo, rendered)

    assert rendered["ready_dispatcher"] == repo["ready_dispatcher"]
    assert rendered["providers"] == {
        "claude": {"account": "shared"},
        "claude2": {"account": "shared"},
    }


def test_apply_provider_account_schema_rejects_unknown_provider_without_account() -> None:
    repo = {
        "ready_dispatcher": {"require_explicit_provider_accounts": True},
        "providers": {"codex": {"account": "codex1"}},
    }
    rendered = {
        "ready_dispatcher": {},
        "providers": {"codex": {}, "unexpected": {}},
    }

    with pytest.raises(ValueError, match="unexpected"):
        apply_provider_account_schema(repo, rendered)


def test_apply_ready_dispatcher_policy_replaces_stale_disabled_capacity_overlay() -> None:
    repo = {
        "ready_dispatcher": {
            "enabled": True,
            "disabled_agents": ["Antigravity2", "Copilot"],
            "sidecar_only_agents": [],
            "target_workload": {"Codex": 30, "Codex2": 30},
            "max_tasks_per_agent_by_agent": {"Codex": 4, "Codex2": 4},
            "max_dispatches_per_tick": 10,
            "max_active_workers_per_task": 1,
            "max_concurrent_per_account": {"codex1": 4, "codex2": 4},
            "max_concurrent_workers": 13,
        }
    }
    rendered = {
        "ready_dispatcher": {
            "enabled": True,
            "disabled_agents": ["Codex", "Codex2"],
            "sidecar_only_agents": ["Codex"],
            "target_workload": {"Codex": 0, "Codex2": 0},
            "max_tasks_per_agent_by_agent": {"Codex": 0, "Codex2": 0},
            "max_dispatches_per_tick": 1,
            "max_active_workers_per_task": 2,
            "max_concurrent_per_account": {"codex1": 0, "codex2": 0},
            "max_concurrent_workers": 1,
            "environment_only_key": "preserved",
        }
    }

    apply_ready_dispatcher_policy(repo, rendered)

    assert rendered["ready_dispatcher"] == {
        **repo["ready_dispatcher"],
        "environment_only_key": "preserved",
    }


def test_apply_supervisor_lease_policy_replaces_stale_live_overlay() -> None:
    repo = {
        "supervisor": {
            "observe_worker_commit_progress": True,
            "lease_requires_work_progress": True,
            "poll_interval_seconds": 30,
        },
        "worker_runtime": {
            "worker_lease_seconds": 600,
            "queue_lease_seconds": 1800,
            "work_progress_stale_seconds": 360,
        },
    }
    rendered = {
        "supervisor": {
            "observe_worker_commit_progress": False,
            "lease_requires_work_progress": False,
            "poll_interval_seconds": 15,
        },
        "worker_runtime": {
            "worker_lease_seconds": 1800,
            "queue_lease_seconds": 900,
            "work_progress_stale_seconds": 900,
        },
    }

    apply_supervisor_lease_policy(repo, rendered)

    assert rendered["supervisor"] == {
        "observe_worker_commit_progress": True,
        "lease_requires_work_progress": True,
        "poll_interval_seconds": 15,
    }
    assert rendered["worker_runtime"] == {
        "worker_lease_seconds": 600,
        "queue_lease_seconds": 900,
        "work_progress_stale_seconds": 360,
    }


def test_apply_coordination_policy_replaces_stale_live_overlay() -> None:
    repo = {
        "coordination": {
            "enabled": False,
        },
    }
    rendered = {
        "coordination": {
            "enabled": True,
            "environment_only_key": "preserved",
        },
    }

    apply_coordination_policy(repo, rendered)

    assert rendered["coordination"] == {
        "enabled": False,
        "environment_only_key": "preserved",
    }


def test_build_live_config_pins_status_paths_and_supervisor_command(tmp_path: Path) -> None:
    command_root = tmp_path / "dev-root"
    status_root = tmp_path / "canonical-root"
    live_config = tmp_path / "runtime" / "live.json"
    python = tmp_path / "bin" / "python3"
    repo_config = {
        "paths": {
            "status_file": "ai-status.json",
            "state_file": ".orchestrator/state.json",
            "activity_log": "ai-activity-log.jsonl",
        },
        "watchdog": {"enabled": True, "supervisor_command": ["stale"]},
        "coordination": {"enabled": False},
        "supervisor": {"lease_requires_work_progress": True},
        "worker_runtime": {"worker_lease_seconds": 600},
        "task_state_store": {
            "mode": "shadow",
            "event_log": ".orchestrator/task-state-events.jsonl",
        },
    }
    existing = {
        "github_bus": {"enabled": False},
        "coordination": {"enabled": True},
        "paths": {"status_file": "/stale/ai-status.json"},
        "supervisor": {"lease_requires_work_progress": False},
        "worker_runtime": {"worker_lease_seconds": 1800},
        "task_state_store": {
            "mode": "off",
            "event_log": "/stale/task-state-events.jsonl",
        },
    }

    rendered = build_live_config(
        repo_config,
        existing_live_config=existing,
        command_root=command_root,
        status_root=status_root,
        live_config_path=live_config,
        python_executable=python,
    )

    assert rendered["paths"] == {
        "status_file": str(status_root / "ai-status.json"),
        "state_file": str(status_root / ".orchestrator" / "state.json"),
        "activity_log": str(status_root / "ai-activity-log.jsonl"),
    }
    assert rendered["coordination"]["enabled"] is False
    assert rendered["github_bus"]["enabled"] is False
    assert rendered["supervisor"]["lease_requires_work_progress"] is True
    assert rendered["worker_runtime"]["worker_lease_seconds"] == 600
    assert rendered["task_state_store"] == {
        "mode": "shadow",
        "event_log": str(live_config.parent / "task-state-events.jsonl"),
    }
    assert rendered["watchdog"]["supervisor_command"] == [
        str(python),
        "-u",
        str(command_root / ".orchestrator" / "supervisor.py"),
        "--config",
        str(live_config),
        "--verbose",
    ]
    assert rendered["watchdog"]["state_file"] == str(
        status_root / ".orchestrator" / "watchdog-state.json"
    )
    assert rendered["watchdog"]["metrics_file"] == str(
        status_root / ".orchestrator" / "metrics" / "supervisor-watchdog.jsonl"
    )
    assert rendered["watchdog"]["contention_metrics_file"] == str(
        status_root
        / ".orchestrator"
        / "metrics"
        / "supervisor-watchdog-contention.jsonl"
    )


def test_task_state_store_rejects_runtime_path_inside_git_roots(tmp_path: Path) -> None:
    status_root = tmp_path / "canonical-root"
    status_root.mkdir()
    rendered: dict[str, object] = {}

    with pytest.raises(ValueError, match="outside the status git root"):
        apply_task_state_store(
            {
                "task_state_store": {
                    "mode": "shadow",
                    "event_log": ".orchestrator/task-state-events.jsonl",
                }
            },
            rendered,
            command_root=tmp_path / "dev-root",
            status_root=status_root,
            live_config_path=status_root / "live.json",
        )


def test_task_state_store_rejects_symlink_event_log(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    real_log = tmp_path / "real-events.jsonl"
    real_log.write_text("", encoding="utf-8")
    (runtime / "task-state-events.jsonl").symlink_to(real_log)

    with pytest.raises(ValueError, match="symlink"):
        apply_task_state_store(
            {
                "task_state_store": {
                    "mode": "shadow",
                    "event_log": ".orchestrator/task-state-events.jsonl",
                }
            },
            {},
            command_root=tmp_path / "dev-root",
            status_root=tmp_path / "status-root",
            live_config_path=runtime / "live.json",
        )


def test_task_state_store_accepts_authoritative_mode_outside_git_roots(tmp_path: Path) -> None:
    command_root = tmp_path / "dev-root"
    status_root = tmp_path / "status-root"
    runtime = tmp_path / "runtime"
    command_root.mkdir()
    status_root.mkdir()
    runtime.mkdir()
    rendered: dict[str, object] = {}

    apply_task_state_store(
        {
            "task_state_store": {
                "mode": "authoritative",
                "event_log": ".orchestrator/task-state-events.jsonl",
            }
        },
        rendered,
        command_root=command_root,
        status_root=status_root,
        live_config_path=runtime / "live.json",
    )

    assert rendered["task_state_store"] == {
        "mode": "authoritative",
        "event_log": str(runtime / "task-state-events.jsonl"),
    }


def test_canonical_watchdog_runtime_paths_preserves_paths_inside_status_root(
    tmp_path: Path,
) -> None:
    status_root = tmp_path / "canonical-root"
    status_root.mkdir()
    custom_state = status_root / ".orchestrator" / "custom-watchdog.json"

    rendered = canonical_watchdog_runtime_paths(
        {"state_file": str(custom_state)},
        status_root,
    )

    assert rendered == {
        "state_file": str(custom_state),
        "metrics_file": str(
            status_root / ".orchestrator" / "metrics" / "supervisor-watchdog.jsonl"
        ),
        "contention_metrics_file": str(
            status_root
            / ".orchestrator"
            / "metrics"
            / "supervisor-watchdog-contention.jsonl"
        ),
    }


def test_canonical_watchdog_runtime_paths_rejects_split_root_escape(
    tmp_path: Path,
) -> None:
    status_root = tmp_path / "canonical-root"
    status_root.mkdir()

    with pytest.raises(ValueError, match="escapes canonical status root"):
        canonical_watchdog_runtime_paths(
            {"state_file": str(tmp_path / "dev-root" / "watchdog-state.json")},
            status_root,
        )


def test_canonical_status_paths_rejects_noncanonical_status_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="status_file"):
        canonical_status_paths(
            {"paths": {"status_file": "/another/root/ai-status.json"}},
            tmp_path,
        )


def test_canonical_status_paths_rejects_relative_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes canonical status root"):
        canonical_status_paths(
            {"paths": {"status_file": "ai-status.json", "state_file": "../state.json"}},
            tmp_path,
        )


def test_write_json_atomic_replaces_regular_file_with_owner_only_mode(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "live.json"
    target.parent.mkdir()
    target.write_text("{}\n", encoding="utf-8")

    write_json_atomic(target, {"paths": {"status_file": "/canonical/ai-status.json"}})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "paths": {"status_file": "/canonical/ai-status.json"}
    }
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_json_atomic_rejects_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "live.json"
    link.symlink_to(real)

    with pytest.raises(ValueError, match="symlink"):
        write_json_atomic(link, {"safe": True})


def test_ensure_approval_queue_marker_creates_v2_owner_only_file(tmp_path: Path) -> None:
    orchestrator = tmp_path / ".orchestrator"
    orchestrator.mkdir()
    target = orchestrator / "approval-queue.json"

    assert ensure_approval_queue_marker(target) is True

    assert validate_approval_queue_marker(target) == {
        "version": 2,
        "updated_at": None,
        "pending": [],
        "history": [],
    }
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_ensure_approval_queue_marker_preserves_existing_approvals(tmp_path: Path) -> None:
    orchestrator = tmp_path / ".orchestrator"
    orchestrator.mkdir()
    target = orchestrator / "approval-queue.json"
    existing = {
        "version": 2,
        "updated_at": "2026-07-20T00:00:00Z",
        "pending": [{"approval_id": "apr-1"}],
        "history": [{"approval_id": "apr-0"}],
    }
    target.write_text(json.dumps(existing) + "\n", encoding="utf-8")

    assert ensure_approval_queue_marker(target) is False
    assert json.loads(target.read_text(encoding="utf-8")) == existing


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"version": 1, "pending": [], "history": []}, "version 2"),
        ({"version": 2, "pending": {}, "history": []}, "pending must be a list"),
        ({"version": 2, "pending": [], "history": {}}, "history must be a list"),
    ],
)
def test_ensure_approval_queue_marker_rejects_invalid_existing_schema(
    tmp_path: Path,
    payload: dict[str, object],
    message: str,
) -> None:
    orchestrator = tmp_path / ".orchestrator"
    orchestrator.mkdir()
    target = orchestrator / "approval-queue.json"
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        ensure_approval_queue_marker(target)


def test_ensure_approval_queue_marker_rejects_symlink(tmp_path: Path) -> None:
    orchestrator = tmp_path / ".orchestrator"
    orchestrator.mkdir()
    real = tmp_path / "real-approval-queue.json"
    real.write_text('{"version":2,"pending":[],"history":[]}\n', encoding="utf-8")
    target = orchestrator / "approval-queue.json"
    target.symlink_to(real)

    with pytest.raises(ValueError, match="non-symlink"):
        ensure_approval_queue_marker(target)


def test_validated_immutable_command_root_composes_promotion_identity_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "a" * 40
    root = tmp_path / "command-runtimes" / commit
    (root / ".orchestrator").mkdir(parents=True)
    (root / "scripts").mkdir()
    for relative in (
        ".orchestrator/supervisor.py",
        "scripts/run-supervisor-watchdog.sh",
        "scripts/promote-supervisor-runtime.sh",
    ):
        (root / relative).write_text("safe\n", encoding="utf-8")
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        provision.runtime_promotion,
        "resolve_candidate_root",
        lambda path: calls.append(("resolve", path)) or root,
    )
    monkeypatch.setattr(
        provision.runtime_promotion,
        "parse_origin_url",
        lambda path: calls.append(("remote", path))
        or "https://github.com/ajoe734/pantheon.git",
    )
    monkeypatch.setattr(
        provision.runtime_promotion,
        "validate_remote_url",
        lambda url: calls.append(("validate_remote", url))
        or SimpleNamespace(slug="ajoe734/pantheon"),
    )
    monkeypatch.setattr(
        provision.runtime_promotion,
        "verify_git_head_and_dev_ancestry",
        lambda path, basename: calls.append(("ancestry", path, basename)) or commit,
    )
    monkeypatch.setattr(
        provision.runtime_promotion,
        "verify_working_tree_cleanliness",
        lambda path, expected_head: calls.append(("clean", path, expected_head))
        or "b" * 40,
    )

    identity = validated_immutable_command_root(root)

    assert identity == {
        "root": str(root),
        "head": commit,
        "tree": "b" * 40,
        "remote": "https://github.com/ajoe734/pantheon.git",
        "repository": "ajoe734/pantheon",
    }
    assert [call[0] for call in calls] == [
        "resolve",
        "remote",
        "validate_remote",
        "ancestry",
        "clean",
    ]


def test_main_bootstraps_split_root_approval_queue_before_watchdog_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_root = tmp_path / "command-runtimes" / ("a" * 40)
    status_root = tmp_path / "canonical-root"
    live_config = tmp_path / "runtime" / "live.json"
    repo_config = command_root / ".orchestrator" / "config.json"
    (command_root / ".git").mkdir(parents=True)
    (command_root / ".orchestrator").mkdir(exist_ok=True)
    (command_root / ".orchestrator" / "supervisor.py").write_text("", encoding="utf-8")
    (command_root / "scripts").mkdir()
    (command_root / "scripts" / "run-supervisor-watchdog.sh").write_text("", encoding="utf-8")
    (command_root / "scripts" / "promote-supervisor-runtime.sh").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        provision,
        "validated_immutable_command_root",
        lambda path: {
            "root": str(path),
            "head": path.name,
            "tree": "b" * 40,
            "remote": "https://github.com/ajoe734/pantheon.git",
            "repository": "ajoe734/pantheon",
        },
    )
    repo_config.write_text(
        json.dumps(
            {
                "paths": {
                    "status_file": "ai-status.json",
                    "state_file": ".orchestrator/state.json",
                    "approval_queue": ".orchestrator/approval-queue.json",
                },
                "watchdog": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (status_root / ".git").mkdir(parents=True)
    (status_root / ".orchestrator").mkdir(exist_ok=True)
    (status_root / "ai-status.json").write_text('{"tasks":[]}\n', encoding="utf-8")

    result = main(
        [
            "--repo-config",
            str(repo_config),
            "--live-config",
            str(live_config),
            "--command-root",
            str(command_root),
            "--status-root",
            str(status_root),
            "--python",
            sys.executable,
            "--json",
        ]
    )

    assert result == 0
    assert validate_approval_queue_marker(
        status_root / ".orchestrator" / "approval-queue.json"
    )["pending"] == []
    assert json.loads(live_config.read_text(encoding="utf-8"))["paths"][
        "approval_queue"
    ] == str(status_root / ".orchestrator" / "approval-queue.json")


def test_main_existing_config_is_noop_or_requires_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_root = tmp_path / "command-runtimes" / ("c" * 40)
    status_root = tmp_path / "status"
    runtime = tmp_path / "runtime"
    repo_config = command_root / ".orchestrator" / "config.json"
    (command_root / ".orchestrator").mkdir(parents=True)
    (status_root / ".orchestrator").mkdir(parents=True)
    runtime.mkdir()
    (command_root / ".orchestrator" / "supervisor.py").write_text("", encoding="utf-8")
    (command_root / "scripts").mkdir()
    for name in ("run-supervisor-watchdog.sh", "promote-supervisor-runtime.sh"):
        (command_root / "scripts" / name).write_text("", encoding="utf-8")
    (status_root / ".git").mkdir()
    (status_root / "ai-status.json").write_text('{"tasks":[]}\n', encoding="utf-8")
    repo_config.write_text(
        json.dumps(
            {
                "paths": {
                    "status_file": "ai-status.json",
                    "approval_queue": ".orchestrator/approval-queue.json",
                },
                "watchdog": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        provision,
        "validated_immutable_command_root",
        lambda path: {
            "root": str(path),
            "head": path.name,
            "tree": "d" * 40,
            "remote": "https://github.com/ajoe734/pantheon.git",
            "repository": "ajoe734/pantheon",
        },
    )
    args = [
        "--repo-config",
        str(repo_config),
        "--live-config",
        str(runtime / "live.json"),
        "--command-root",
        str(command_root),
        "--status-root",
        str(status_root),
        "--python",
        sys.executable,
    ]

    assert main(args) == 0
    live_config = runtime / "live.json"
    before = live_config.read_bytes()
    inode = live_config.stat().st_ino
    assert main(args) == 0
    assert live_config.read_bytes() == before
    assert live_config.stat().st_ino == inode

    changed = json.loads(before)
    changed["watchdog"]["supervisor_command"][2] = "/mutable/dev-root/.orchestrator/supervisor.py"
    live_config.write_text(json.dumps(changed) + "\n", encoding="utf-8")
    drifted = live_config.read_bytes()

    assert main(args) == 2
    assert live_config.read_bytes() == drifted
