from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from provision_live_supervisor_config import (
    build_live_config,
    canonical_status_paths,
    ensure_approval_queue_marker,
    main,
    validate_approval_queue_marker,
    write_json_atomic,
)


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
        "coordination": {"enabled": True},
    }
    existing = {
        "github_bus": {"enabled": False},
        "coordination": {"enabled": False},
        "paths": {"status_file": "/stale/ai-status.json"},
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
    assert rendered["watchdog"]["supervisor_command"] == [
        str(python),
        "-u",
        str(command_root / ".orchestrator" / "supervisor.py"),
        "--config",
        str(live_config),
        "--verbose",
    ]


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


def test_main_bootstraps_split_root_approval_queue_before_watchdog_config(
    tmp_path: Path,
) -> None:
    command_root = tmp_path / "dev-root"
    status_root = tmp_path / "canonical-root"
    live_config = tmp_path / "runtime" / "live.json"
    repo_config = command_root / ".orchestrator" / "config.json"
    (command_root / ".git").mkdir(parents=True)
    (command_root / ".orchestrator").mkdir(exist_ok=True)
    (command_root / ".orchestrator" / "supervisor.py").write_text("", encoding="utf-8")
    (command_root / "scripts").mkdir()
    (command_root / "scripts" / "run-supervisor-watchdog.sh").write_text("", encoding="utf-8")
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
