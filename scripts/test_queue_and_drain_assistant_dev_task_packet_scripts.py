from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRAIN_SCRIPT = ROOT / "scripts" / "drain_assistant_dev_task_packet_inbox.py"
QUEUE_SCRIPT = ROOT / "scripts" / "queue_assistant_dev_task_packet.py"


def test_scripts_prefer_pantheon_status_root(tmp_path: Path) -> None:
    status_root = tmp_path / "status_root"
    status_root.mkdir()

    env = dict(os.environ)
    env["PANTHEON_STATUS_ROOT"] = str(status_root)

    # Run queue script with --help
    res_queue = subprocess.run(
        [sys.executable, str(QUEUE_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert "Verify and queue" in res_queue.stdout

    # Run drain script with --dry-run
    res_drain = subprocess.run(
        [sys.executable, str(DRAIN_SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert res_drain.returncode == 0

    # Ensure no .orchestrator directory was created in current working directory / worktree
    # when PANTHEON_STATUS_ROOT is specified
    cwd_orchestrator = tmp_path / ".orchestrator"
    assert not cwd_orchestrator.exists()
