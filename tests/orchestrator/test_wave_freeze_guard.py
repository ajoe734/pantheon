"""Tests for the wave frozen stage and assignment freeze guard."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_DIR = REPO_ROOT / ".orchestrator"
SCRIPTS_DIR = REPO_ROOT / "scripts"
for import_path in (ORCHESTRATOR_DIR, SCRIPTS_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import ai_status
from wave_guards import (
    WaveGuardError,
    check_current_wave_closed,
    check_wave_assign,
    check_wave_close,
    check_wave_freeze,
)


def test_freeze_requires_open_wave() -> None:
    check_wave_freeze({"current_wave_id": "2026-W25", "status": "open"}, "Codex")

    with pytest.raises(WaveGuardError, match="Freeze-state guard"):
        check_wave_freeze({"current_wave_id": "2026-W25", "status": "closed"}, "Codex")


def test_close_requires_frozen_wave() -> None:
    with pytest.raises(WaveGuardError, match="Freeze-before-close guard"):
        check_wave_close({"current_wave_id": "2026-W25", "status": "open"}, "Codex")


def test_close_requires_thirty_minute_freeze_duration() -> None:
    wave_state = {
        "current_wave_id": "2026-W25",
        "status": "frozen",
        "frozen_at": "2026-05-19T10:00:00Z",
    }

    check_wave_close(
        wave_state,
        "Codex",
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )

    with pytest.raises(WaveGuardError, match="Freeze-duration guard"):
        check_wave_close(
            wave_state,
            "Codex",
            now=datetime(2026, 5, 19, 10, 29, 59, tzinfo=timezone.utc),
        )


def test_close_fails_closed_without_freeze_timestamp() -> None:
    with pytest.raises(WaveGuardError, match="frozen_at is required"):
        check_wave_close({"current_wave_id": "2026-W25", "status": "frozen"}, "Codex")


def test_frozen_wave_is_not_closed_for_successor_open() -> None:
    with pytest.raises(WaveGuardError, match="Current-wave-frozen guard"):
        check_current_wave_closed({"current_wave_id": "2026-W25", "status": "frozen"})


def test_wave_assign_rejects_frozen_state_only() -> None:
    check_wave_assign({"current_wave_id": "2026-W25", "status": "open"})
    check_wave_assign({"current_wave_id": "2026-W25", "status": "closed"})

    with pytest.raises(WaveGuardError, match="Assign-frozen guard"):
        check_wave_assign({"current_wave_id": "2026-W25", "status": "frozen"})


def test_command_assign_rejects_frozen_wave_without_mutating_tasks() -> None:
    state = {
        "agents": [],
        "tasks": [],
        "wave_state": {"current_wave_id": "2026-W25", "status": "frozen"},
    }

    with (
        mock.patch.dict(os.environ, {"AI_NAME": "Codex"}, clear=False),
        mock.patch.object(ai_status, "append_log"),
        mock.patch.object(ai_status, "archived_task_snapshot", return_value=None),
    ):
        with pytest.raises(SystemExit, match="Wave guard rejected assign"):
            ai_status.command_assign(state, ["NEW-TASK", "Codex", "Claude", "New task"])

    assert state["tasks"] == []
