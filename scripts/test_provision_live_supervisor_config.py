from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from provision_live_supervisor_config import (
    build_live_config,
    canonical_status_paths,
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
