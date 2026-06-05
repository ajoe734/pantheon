#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import supervisor_watchdog


class SupervisorWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.root = Path(self.tmpdir.name)
        self.state_file = self.root / "state.json"
        self.activity_log = self.root / "activity-log.jsonl"
        self.config = {
            "paths": {
                "state_file": str(self.state_file),
                "activity_log": str(self.activity_log),
            },
            "watchdog": {
                "state_file": str(self.root / "watchdog-state.json"),
                "metrics_file": str(self.root / "metrics.jsonl"),
                "heartbeat_stale_seconds": 900,
                "restart_budget_window_seconds": 900,
                "max_restarts_per_window": 2,
                "max_restarts_per_hour": 4,
                "backoff_schedule_seconds": [0, 0, 0],
                "circuit_cooldown_seconds": 1800,
                "safe_mode_seconds": 120,
                "min_disk_free_gb": 2.0,
                "max_disk_used_percent": 95.0,
                "min_memory_available_mb": 512,
                "max_load_1m": 24.0,
                "max_active_workers": 12,
            },
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.activity_log.write_text("", encoding="utf-8")

    def write_state(self, payload: dict) -> None:
        self.state_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def write_pid(self, pid: int) -> None:
        (self.state_file.parent / "supervisor.pid").write_text(f"{pid}\n", encoding="utf-8")

    def ok_resource(self) -> dict:
        return {
            "disk_free_gb": 10.0,
            "disk_used_percent": 50.0,
            "memory_available_mb": 4096,
            "load_1m": 1.0,
            "active_worker_count": 0,
            "state_parent_writable": True,
        }

    def test_healthy_supervisor_observes_only(self) -> None:
        now = datetime.now(timezone.utc)
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": supervisor_watchdog.isoformat_utc(now), "lifecycle": "running"}})

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "observe_only")
        self.assertEqual(result["reason"], "supervisor_healthy")

    def test_resource_pressure_suppresses_restart_and_opens_circuit(self) -> None:
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})
        pressure = self.ok_resource()
        pressure["disk_free_gb"] = 0.5

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=pressure),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "suppress_restart")
        self.assertIn("resource_pressure", result["reason"])
        watchdog_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertTrue(watchdog_state["circuit"]["open"])

    def test_unhealthy_supervisor_restarts_with_safe_mode(self) -> None:
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})
        log_path = self.root / "restart.log"

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
            mock.patch.object(supervisor_watchdog, "start_supervisor", return_value=(999, log_path)),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "restart_supervisor")
        self.assertEqual(result["new_pid"], 999)
        runtime_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertIn("safe_mode_until", runtime_state["watchdog"])
        self.assertEqual(runtime_state["watchdog"]["safe_mode_reason"], "pid_not_alive")
        watchdog_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertEqual(watchdog_state["restart_attempts"][0]["new_pid"], 999)

    def test_restart_budget_suppresses_after_window_exhausted(self) -> None:
        now = datetime.now(timezone.utc)
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})
        (self.root / "watchdog-state.json").write_text(
            json.dumps(
                {
                    "restart_attempts": [
                        {"at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=120)), "reason": "pid_not_alive"},
                        {"at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)), "reason": "pid_not_alive"},
                    ],
                    "circuit": {"open": False, "reason": None, "opened_at": None, "until": None},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "suppress_restart")
        self.assertEqual(result["reason"], "restart_budget_window_exhausted")
        watchdog_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertTrue(watchdog_state["circuit"]["open"])

    def hold_lock(self, pid: int = 999):
        """Create supervisor.lock and hold an exclusive flock for the test's lifetime."""
        lock_path = self.state_file.parent / "supervisor.lock"
        lock_path.write_text(f"{pid}\n", encoding="utf-8")
        handle = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)
        return handle

    def test_supervisor_lock_held_true_when_locked(self) -> None:
        self.hold_lock()
        self.assertTrue(supervisor_watchdog.supervisor_lock_held(self.config))

    def test_supervisor_lock_held_false_when_absent_or_free(self) -> None:
        # No lock file at all.
        self.assertFalse(supervisor_watchdog.supervisor_lock_held(self.config))
        # File present but nobody holds the flock.
        (self.state_file.parent / "supervisor.lock").write_text("0\n", encoding="utf-8")
        self.assertFalse(supervisor_watchdog.supervisor_lock_held(self.config))

    def test_lock_held_with_missing_pid_observes_only(self) -> None:
        """Regression: clean-restart seam (pid file gone) while the flock is held
        must NOT trigger a missing_pid restart."""
        now = datetime.now(timezone.utc)
        self.hold_lock()
        # Deliberately do NOT write supervisor.pid -> read_pid_file returns None.
        self.write_state({"supervisor": {"last_heartbeat_at": supervisor_watchdog.isoformat_utc(now), "lifecycle": "running"}})

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
            mock.patch.object(supervisor_watchdog, "start_supervisor", return_value=(999, self.root / "r.log")),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "observe_only")
        self.assertEqual(result["reason"], "supervisor_healthy")
        self.assertTrue(result["lock_held"])

    def test_no_lock_and_missing_pid_restarts(self) -> None:
        """No flock held AND no pid file -> genuinely dead -> restart with missing_pid."""
        now = datetime.now(timezone.utc)
        # No lock file, no pid file.
        self.write_state({"supervisor": {"last_heartbeat_at": supervisor_watchdog.isoformat_utc(now), "lifecycle": "running"}})

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
            mock.patch.object(supervisor_watchdog, "start_supervisor", return_value=(999, self.root / "r.log")),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "restart_supervisor")
        self.assertEqual(result["reason"], "missing_pid")
        self.assertFalse(result["lock_held"])


if __name__ == "__main__":
    unittest.main()
