"""Portable Human/Ops entry binding; no live TaskStore is touched."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _wrapper(tmp_path: Path) -> Path:
    scripts = tmp_path / "commands" / "scripts"
    scripts.mkdir(parents=True)
    wrapper = scripts / "human-ops-status.sh"
    shutil.copy2(ROOT / "scripts" / wrapper.name, wrapper)
    target = scripts / "ai-status.sh"
    target.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$AI_NAME\" \"$PANTHEON_LOCAL_HUMAN_OPS\" "
        "\"${PANTHEON_STATUS_ROOT:-}\" \"${PANTHEON_TASK_STATE_EVENT_LOG:-}\" "
        "\"${PANTHEON_TASK_STATE_STORE_MODE:-}\" \"$*\"\n"
    )
    target.chmod(0o755)
    orchestrator = scripts.parent / ".orchestrator"
    orchestrator.mkdir()
    (orchestrator / "common.py").write_text(
        "def canonical_task_state_identity(config):\n"
        "    return config['test_identity']\n"
    )
    return wrapper


def _config(path: Path, root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"test_identity": {
        "status_root": str(root), "event_log": str(root / "journal.jsonl"),
    }}))


def _env(deploy_root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("PANTHEON_")}
    env["PANTHEON_DEPLOY_ROOT"] = str(deploy_root)
    return env


def test_uses_deploy_root_without_a_named_users_home(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)
    deploy_root = tmp_path / "current account deploy"
    state_root = tmp_path / "canonical-state"
    _config(deploy_root / "runtime/live-supervisor-mainroot-config.json", state_root)
    env = _env(deploy_root)
    env["PANTHEON_STATUS_ROOT"] = str(tmp_path / "wrong-state")
    result = subprocess.run(
        [str(wrapper), "note", "TASK-1", "maintenance"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout.splitlines() == [
        "Human/Ops", "1", str(state_root), str(state_root / "journal.jsonl"),
        "authoritative", "note TASK-1 maintenance",
    ]


def test_explicit_live_config_override_still_wins(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)
    deploy_root = tmp_path / "deploy"
    _config(deploy_root / "runtime/live-supervisor-mainroot-config.json", tmp_path / "default-state")
    explicit = tmp_path / "isolated-config.json"
    _config(explicit, tmp_path / "isolated-state")
    env = _env(deploy_root)
    env["PANTHEON_LIVE_SUPERVISOR_CONFIG"] = str(explicit)
    result = subprocess.run(
        [str(wrapper), "show"], env=env,
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.splitlines()[2] == str(tmp_path / "isolated-state")


def test_invalid_canonical_config_does_not_run_status_command(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)
    deploy_root = tmp_path / "deploy"
    live_config = deploy_root / "runtime/live-supervisor-mainroot-config.json"
    live_config.parent.mkdir(parents=True)
    live_config.write_text("not json")
    result = subprocess.run(
        [str(wrapper), "show"], env=_env(deploy_root),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Failed to derive canonical Human/Ops task-state binding" in result.stderr


def test_default_matches_runtime_bootstrap_deploy_layout() -> None:
    source = (ROOT / "scripts/human-ops-status.sh").read_text()
    assert 'DEPLOY_ROOT="${PANTHEON_DEPLOY_ROOT:-$HOME/pantheon-ci-deploy}"' in source
    assert "/home/lupin" not in source
