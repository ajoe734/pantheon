#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import supervisor_watchdog  # noqa: E402


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
                "contention_metrics_file": str(self.root / "metrics-contention.jsonl"),
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

    def test_public_watchdog_state_save_holds_runtime_sidecar_and_reads_back(self) -> None:
        real_write = supervisor_watchdog._write_watchdog_json_locked
        lock_path = self.root / ".orchestrator" / "runtime-admission.lock"

        def assert_locked_write(path: Path, payload: dict, *, label: str) -> None:
            probe = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe)
            real_write(path, payload, label=label)

        state = {"restart_attempts": [], "circuit": {"open": False}}
        with mock.patch.object(
            supervisor_watchdog,
            "_write_watchdog_json_locked",
            side_effect=assert_locked_write,
        ):
            supervisor_watchdog.save_watchdog_state(self.config, state)

        saved = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, state)
        self.assertEqual(saved["version"], 1)
        self.assertIsNotNone(saved["updated_at"])

    def test_public_watchdog_metric_append_holds_runtime_sidecar(self) -> None:
        real_append = supervisor_watchdog._append_watchdog_jsonl_locked
        lock_path = self.root / ".orchestrator" / "runtime-admission.lock"

        def assert_locked_append(path: Path, payload: dict, *, label: str) -> None:
            probe = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(probe)
            real_append(path, payload, label=label)

        with mock.patch.object(
            supervisor_watchdog,
            "_append_watchdog_jsonl_locked",
            side_effect=assert_locked_append,
        ):
            supervisor_watchdog.append_watchdog_metric(self.config, {"event_type": "probe"})

        row = json.loads((self.root / "metrics.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(row["event_type"], "probe")
        self.assertEqual(row["version"], 1)

    def test_watchdog_state_rejects_symlink_leaf_without_touching_target(self) -> None:
        target = self.root / "outside-state.json"
        target.write_text('{"operator": true}\n', encoding="utf-8")
        (self.root / "watchdog-state.json").symlink_to(target)

        with self.assertRaisesRegex(RuntimeError, "data leaf cannot be a symlink"):
            supervisor_watchdog.save_watchdog_state(self.config, {"restart_attempts": []})

        self.assertEqual(target.read_text(encoding="utf-8"), '{"operator": true}\n')

    def test_watchdog_metrics_rejects_symlink_leaf_without_touching_target(self) -> None:
        target = self.root / "outside-metrics.jsonl"
        target.write_text('{"operator": true}\n', encoding="utf-8")
        (self.root / "metrics.jsonl").symlink_to(target)

        with self.assertRaisesRegex(RuntimeError, "data leaf cannot be a symlink"):
            supervisor_watchdog.append_watchdog_metric(self.config, {"event_type": "probe"})

        self.assertEqual(target.read_text(encoding="utf-8"), '{"operator": true}\n')

    def test_watchdog_state_save_fails_closed_on_readback_mismatch(self) -> None:
        with mock.patch.object(supervisor_watchdog, "_read_watchdog_bytes", return_value=b"corrupt"):
            with self.assertRaisesRegex(RuntimeError, "readback mismatch"):
                supervisor_watchdog.save_watchdog_state(self.config, {"restart_attempts": []})

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

    def test_pressure_circuit_early_closes_once_pressure_clears(self) -> None:
        # Case A: circuit opened for a transient load spike; next tick reports
        # clean pressure -> the circuit must early-close and allow a restart,
        # not wait out the full 30-minute cooldown.
        now = datetime.now(timezone.utc)
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})
        (self.root / "watchdog-state.json").write_text(
            json.dumps(
                {
                    "restart_attempts": [],
                    "circuit": {
                        "open": True,
                        "reason": "resource_pressure:load_above_threshold",
                        "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)),
                        "until": supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700)),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        log_path = self.root / "restart.log"

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
            mock.patch.object(supervisor_watchdog, "start_supervisor", return_value=(999, log_path)),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "restart_supervisor")
        watchdog_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertFalse(watchdog_state["circuit"]["open"])

    def test_pressure_circuit_early_closes_on_healthy_tick(self) -> None:
        now = datetime.now(timezone.utc)
        self.write_pid(123)
        self.write_state(
            {
                "supervisor": {
                    "pid": 123,
                    "last_heartbeat_at": supervisor_watchdog.isoformat_utc(now),
                    "lifecycle": "running",
                }
            }
        )
        (self.root / "watchdog-state.json").write_text(
            json.dumps(
                {
                    "restart_attempts": [],
                    "circuit": {
                        "open": True,
                        "reason": "resource_pressure:load_above_threshold",
                        "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)),
                        "until": supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700)),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "observe_only")
        self.assertEqual(result["reason"], "supervisor_healthy")
        watchdog_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertFalse(watchdog_state["circuit"]["open"])

    def test_non_pressure_circuit_stays_suppressed_during_cooldown(self) -> None:
        # Case B: circuit opened for a genuine crash-loop reason (restart
        # budget exhausted); pressure being clean this tick must NOT early-
        # close it, since that is not a resource_pressure circuit.
        now = datetime.now(timezone.utc)
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})
        (self.root / "watchdog-state.json").write_text(
            json.dumps(
                {
                    "restart_attempts": [],
                    "circuit": {
                        "open": True,
                        "reason": "restart_budget_window_exhausted",
                        "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)),
                        "until": supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700)),
                    },
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
        self.assertEqual(result["reason"], "watchdog_circuit_open")
        watchdog_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertTrue(watchdog_state["circuit"]["open"])

    def test_circuit_stays_open_while_pressure_persists(self) -> None:
        # Case C: pressure is still present this tick -> must remain
        # suppressed regardless of the early-close change.
        now = datetime.now(timezone.utc)
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})
        (self.root / "watchdog-state.json").write_text(
            json.dumps(
                {
                    "restart_attempts": [],
                    "circuit": {
                        "open": True,
                        "reason": "resource_pressure:load_above_threshold",
                        "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)),
                        "until": supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700)),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        pressure = self.ok_resource()
        pressure["load_1m"] = 99.0

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=pressure),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "suppress_restart")
        self.assertIn("resource_pressure", result["reason"])
        watchdog_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertTrue(watchdog_state["circuit"]["open"])

    def test_non_pressure_circuit_survives_pressure_then_clear(self) -> None:
        now = datetime.now(timezone.utc)
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})
        original_until = supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700))
        (self.root / "watchdog-state.json").write_text(
            json.dumps(
                {
                    "restart_attempts": [],
                    "circuit": {
                        "open": True,
                        "reason": "restart_budget_window_exhausted",
                        "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)),
                        "until": original_until,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        pressure = self.ok_resource()
        pressure["load_1m"] = 99.0

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=pressure),
        ):
            pressure_result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(pressure_result["decision"], "suppress_restart")
        pressure_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertEqual(pressure_state["circuit"]["reason"], "restart_budget_window_exhausted")
        self.assertEqual(pressure_state["circuit"]["until"], original_until)

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
        ):
            cleared_result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(cleared_result["decision"], "suppress_restart")
        self.assertEqual(cleared_result["reason"], "watchdog_circuit_open")
        cleared_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertTrue(cleared_state["circuit"]["open"])
        self.assertEqual(cleared_state["circuit"]["reason"], "restart_budget_window_exhausted")

    def test_early_close_helper_closes_cleared_pressure_circuit(self) -> None:
        now = datetime.now(timezone.utc)
        watchdog_state = {
            "circuit": {
                "open": True,
                "reason": "resource_pressure:load_above_threshold",
                "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)),
                "until": supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700)),
            },
            "restart_attempts": [],
        }
        closed = supervisor_watchdog.early_close_cleared_pressure_circuit(watchdog_state, now, pressure_reasons=[])
        self.assertTrue(closed)
        self.assertFalse(watchdog_state["circuit"]["open"])

    def test_early_close_helper_keeps_non_pressure_circuit_open(self) -> None:
        now = datetime.now(timezone.utc)
        watchdog_state = {
            "circuit": {
                "open": True,
                "reason": "restart_budget_window_exhausted",
                "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)),
                "until": supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700)),
            },
            "restart_attempts": [],
        }
        closed = supervisor_watchdog.early_close_cleared_pressure_circuit(watchdog_state, now, pressure_reasons=[])
        self.assertFalse(closed)
        self.assertTrue(watchdog_state["circuit"]["open"])

    def test_early_close_helper_requires_explicit_clean_scan(self) -> None:
        now = datetime.now(timezone.utc)
        watchdog_state = {
            "circuit": {
                "open": True,
                "reason": "resource_pressure:load_above_threshold",
                "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=60)),
                "until": supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700)),
            },
            "restart_attempts": [],
        }
        closed = supervisor_watchdog.early_close_cleared_pressure_circuit(watchdog_state, now)
        self.assertFalse(closed)
        self.assertTrue(watchdog_state["circuit"]["open"])

    def test_budget_suppression_reason_closes_expired_circuit(self) -> None:
        now = datetime.now(timezone.utc)
        watchdog_state = {
            "circuit": {
                "open": True,
                "reason": "restart_budget_window_exhausted",
                "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=1900)),
                "until": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=100)),
            },
            "restart_attempts": [],
        }
        reason = supervisor_watchdog.budget_suppression_reason(watchdog_state, now, self.config["watchdog"])
        self.assertIsNone(reason)
        self.assertFalse(watchdog_state["circuit"]["open"])

    def hold_lock(self, pid: int = 999):
        """Create supervisor.lock and hold an exclusive flock for the test's lifetime."""
        lock_path = self.state_file.parent / "supervisor.lock"
        lock_path.write_text(f"{pid}\n", encoding="utf-8")
        handle = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)
        return handle

    def test_start_supervisor_pins_status_root_from_config(self) -> None:
        # Regression: the supervisor runs from the dev-root checkout but must
        # resolve the task archive against the canonical worktree named in
        # config.paths.status_file, or freshly-archived dependencies read as
        # "missing" and ready-dispatch stalls to a single worker.
        # docs/decisions/supervisor-status-root-split-brain-2026-06-09.md
        status_file = self.root / "canonical" / "ai-status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        self.config["paths"]["status_file"] = str(status_file)
        captured = {}

        class _FakeProc:
            pid = 4242

        def _fake_popen(command, **kwargs):
            captured["env"] = kwargs.get("env")
            return _FakeProc()

        now = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
        with mock.patch.object(supervisor_watchdog.subprocess, "Popen", _fake_popen):
            pid, _log_path = supervisor_watchdog.start_supervisor(self.config, {}, now)
        self.assertEqual(pid, 4242)
        self.assertIsNotNone(captured["env"])
        self.assertEqual(
            captured["env"].get("PANTHEON_STATUS_ROOT"),
            str(status_file.parent),
        )

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

    def test_lock_contention_returns_skip_immediately(self) -> None:
        """When the runtime-admission lock is held, run_watchdog returns skip/lock_contention immediately without blocking or modifying files."""
        lock_dir = self.root / ".orchestrator"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "runtime-admission.lock"

        lock_handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(lock_handle.close)

        self.write_pid(123)
        self.write_state({
            "supervisor": {
                "pid": 123,
                "last_heartbeat_at": "2026-05-18T13:00:00Z",
                "lifecycle": "running"
            }
        })

        with mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "skip")
        self.assertEqual(result["reason"], "lock_contention")
        self.assertEqual(result["pid"], 123)

        watchdog_state_file = self.root / "watchdog-state.json"
        self.assertFalse(watchdog_state_file.exists())

        # Verify contention metric write
        contention_file = self.root / "metrics-contention.jsonl"
        self.assertTrue(contention_file.exists())
        lines = contention_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["decision"], "skip")
        self.assertEqual(event["reason"], "lock_contention")

    def test_lock_contention_multi_tick_bounded(self) -> None:
        """Simulate 10+ cron ticks under contention: all exit immediately with skip, leaving the lock untouched."""
        lock_dir = self.root / ".orchestrator"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "runtime-admission.lock"

        lock_handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(lock_handle.close)

        self.write_pid(123)
        self.write_state({
            "supervisor": {
                "pid": 123,
                "last_heartbeat_at": "2026-05-18T13:00:00Z",
                "lifecycle": "running"
            }
        })

        for _ in range(12):
            with mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()):
                result = supervisor_watchdog.run_watchdog(self.config, restart=True)
            self.assertEqual(result["decision"], "skip")
            self.assertEqual(result["reason"], "lock_contention")

    def test_lock_contention_subprocess_launches(self) -> None:
        """Spawn 12 concurrent watchdog processes via subprocess while the lock is held,
        proving they all exit immediately with exit code 0, do not accumulate, and write to contention metrics."""
        lock_dir = self.root / ".orchestrator"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "runtime-admission.lock"

        lock_handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(lock_handle.close)

        # Write config.json under self.root/.orchestrator so scripts can find it
        config_path = self.root / ".orchestrator" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f)

        self.write_pid(123)
        self.write_state({
            "supervisor": {
                "pid": 123,
                "last_heartbeat_at": "2026-05-18T13:00:00Z",
                "lifecycle": "running"
            }
        })

        import subprocess
        processes = []
        for _ in range(12):
            p = subprocess.Popen(
                [sys.executable, str(Path(supervisor_watchdog.__file__).resolve()), "--config", str(config_path), "--json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append(p)

        outputs = []
        for p in processes:
            stdout, stderr = p.communicate(timeout=5.0)
            outputs.append((p.returncode, stdout, stderr))

        for code, stdout, stderr in outputs:
            self.assertEqual(code, 0, f"Subprocess failed with stderr: {stderr.decode()}")
            data = json.loads(stdout.decode())
            self.assertEqual(data["decision"], "skip")
            self.assertEqual(data["reason"], "lock_contention")

        # Contention metrics file should contain the events that were not dropped
        contention_file = self.root / "metrics-contention.jsonl"
        lines = []
        if contention_file.exists():
            lines = contention_file.read_text(encoding="utf-8").splitlines()

        dropped_count = sum(
            1 for _, _, stderr in outputs
            if "watchdog contention metric write dropped due to lock contention" in stderr.decode()
        )
        self.assertEqual(len(lines) + dropped_count, 12, "Total metric events (written + dropped) should equal 12")

        for line in lines:
            event = json.loads(line)
            self.assertEqual(event["decision"], "skip")
            self.assertEqual(event["reason"], "lock_contention")

    def test_metric_lock_contention_subprocess_launches(self) -> None:
        """Spawn concurrent watchdog processes via subprocess while BOTH the primary lock and the metric lock are held,
        proving they all exit immediately within a deadline, do not block, and their metric writes are dropped."""
        lock_dir = self.root / ".orchestrator"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "runtime-admission.lock"

        # Hold primary lock
        lock_handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(lock_handle.close)

        # Hold secondary metric lock
        metric_lock_path = self.root / "metrics-contention.lock"
        metric_lock_path.parent.mkdir(parents=True, exist_ok=True)
        metric_lock_handle = open(metric_lock_path, "w", encoding="utf-8")
        fcntl.flock(metric_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(metric_lock_handle.close)

        # Write config.json under self.root/.orchestrator so scripts can find it
        config_path = self.root / ".orchestrator" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f)

        self.write_pid(123)
        self.write_state({
            "supervisor": {
                "pid": 123,
                "last_heartbeat_at": "2026-05-18T13:00:00Z",
                "lifecycle": "running"
            }
        })

        import subprocess
        processes = []
        for _ in range(5):
            p = subprocess.Popen(
                [sys.executable, str(Path(supervisor_watchdog.__file__).resolve()), "--config", str(config_path), "--json"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append(p)

        outputs = []
        for p in processes:
            # Enforce a strict timeout deadline of 2.0 seconds
            stdout, stderr = p.communicate(timeout=2.0)
            outputs.append((p.returncode, stdout, stderr))

        for code, stdout, stderr in outputs:
            self.assertEqual(code, 0, f"Subprocess failed with stderr: {stderr.decode()}")
            data = json.loads(stdout.decode())
            self.assertEqual(data["decision"], "skip")
            self.assertEqual(data["reason"], "lock_contention")
            self.assertIn("watchdog contention metric write dropped due to lock contention", stderr.decode())

        # Contention metrics file should remain empty or nonexistent because all writes were dropped
        contention_file = self.root / "metrics-contention.jsonl"
        if contention_file.exists():
            self.assertEqual(contention_file.read_text(encoding="utf-8"), "")

    def test_lock_release_and_probe_updates_state(self) -> None:
        """After releasing the lock, a subsequent probe succeeds, updates the state files, and is healthy."""
        lock_dir = self.root / ".orchestrator"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "runtime-admission.lock"

        lock_handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()):
            result1 = supervisor_watchdog.run_watchdog(self.config, restart=True)
        self.assertEqual(result1["decision"], "skip")

        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()

        now = datetime.now(timezone.utc)
        self.write_pid(123)
        self.write_state({
            "supervisor": {
                "pid": 123,
                "last_heartbeat_at": supervisor_watchdog.isoformat_utc(now),
                "lifecycle": "running"
            }
        })

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
        ):
            result2 = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result2["decision"], "observe_only")
        self.assertEqual(result2["reason"], "supervisor_healthy")

        watchdog_state_file = self.root / "watchdog-state.json"
        self.assertTrue(watchdog_state_file.exists())

        # Validate with supervisor_runtime_health.py --require-watchdog --json
        # We lock supervisor.lock to simulate that the supervisor process is alive
        sup_lock_path = self.state_file.parent / "supervisor.lock"
        sup_lock_handle = open(sup_lock_path, "w", encoding="utf-8")
        fcntl.flock(sup_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        config_path = self.root / ".orchestrator" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f)

        import subprocess
        health_script_path = Path(__file__).resolve().parent.parent / "scripts" / "supervisor_runtime_health.py"
        p = subprocess.Popen(
            [sys.executable, str(health_script_path), "--repo", str(self.root), "--require-watchdog", "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = p.communicate(timeout=5.0)

        fcntl.flock(sup_lock_handle.fileno(), fcntl.LOCK_UN)
        sup_lock_handle.close()

        self.assertEqual(p.returncode, 0, f"Health check failed with stderr: {stderr.decode()}")
        health_report = json.loads(stdout.decode())
        self.assertTrue(health_report["healthy"])


class ActiveWorkerCountDedupeTests(unittest.TestCase):
    """Watchdog restart pressure must use live wrapper identities, not stale state."""

    def write_fake_process(self, proc_root: Path, pid: int, parts: list[str], starttime: int) -> None:
        proc_dir = proc_root / str(pid)
        proc_dir.mkdir(parents=True)
        (proc_dir / "cmdline").write_bytes(b"\x00".join(part.encode("utf-8") for part in parts) + b"\x00")
        suffix = ["S", *(["0"] * 18), str(starttime), "0"]
        (proc_dir / "stat").write_text(f"{pid} (worker runner) {' '.join(suffix)}\n", encoding="utf-8")

    def test_counts_one_per_worker_run_regardless_of_os_process_count(self) -> None:
        runtime_state = {
            "workers": {
                "run-1": {"run_id": "run-1", "agent_id": "claude", "status": "running"},
                "run-2": {"run_id": "run-2", "agent_id": "codex", "status": "waiting_approval"},
                "run-3": {"run_id": "run-3", "agent_id": "gemini", "status": "done"},
            }
        }
        self.assertEqual(supervisor_watchdog.active_worker_count(runtime_state), 2)

    def test_live_scan_counts_only_unique_worker_runner_pid_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp)
            self.write_fake_process(
                proc_root,
                101,
                ["/usr/bin/python3", "/repo/.orchestrator/worker_runner.py", "--run-id", "run-1"],
                9001,
            )
            self.write_fake_process(
                proc_root,
                102,
                ["node", "/bin/codex", "prompt mentions /repo/.orchestrator/worker_runner.py"],
                9002,
            )
            self.write_fake_process(
                proc_root,
                103,
                ["/usr/bin/python3", "-u", "/repo/.orchestrator/worker_runner.py", "--run-id", "run-2"],
                9003,
            )

            identities, error = supervisor_watchdog.scan_live_worker_runner_identities(proc_root)

        self.assertIsNone(error)
        self.assertEqual(identities, {(101, 9001), (103, 9003)})

    def test_resource_snapshot_ignores_stale_runtime_rows_when_live_scan_succeeds(self) -> None:
        runtime_state = {
            "workers": {
                f"stale-{index}": {"status": "running"}
                for index in range(16)
            }
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                supervisor_watchdog,
                "scan_live_worker_runner_identities",
                return_value=({(101, 9001), (103, 9003)}, None),
            ),
        ):
            config = {"paths": {"state_file": str(Path(tmp) / "state.json")}}
            snapshot = supervisor_watchdog.resource_snapshot(config, runtime_state, {})

        self.assertEqual(snapshot["active_worker_count"], 2)
        self.assertEqual(snapshot["active_worker_live_count"], 2)
        self.assertEqual(snapshot["active_worker_runtime_state_count"], 16)
        self.assertEqual(snapshot["active_worker_count_source"], "live_worker_runner_pid_identity")
        self.assertIsNone(snapshot["active_worker_scan_error"])

    def test_failed_live_scan_fails_closed_with_recorded_count(self) -> None:
        runtime_state = {"workers": {"stale": {"status": "running"}}}
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                supervisor_watchdog,
                "scan_live_worker_runner_identities",
                return_value=(set(), "proc_scan_failed:PermissionError"),
            ),
        ):
            config = {"paths": {"state_file": str(Path(tmp) / "state.json")}}
            snapshot = supervisor_watchdog.resource_snapshot(config, runtime_state, {})

        self.assertEqual(snapshot["active_worker_count"], 1)
        self.assertEqual(snapshot["active_worker_count_source"], "fail_safe_max_live_and_runtime_state")
        reasons = supervisor_watchdog.resource_pressure_reasons(snapshot, {**self._pressure_settings(), "max_active_workers": 12})
        self.assertIn("active_worker_scan_failed", reasons)

    @staticmethod
    def _pressure_settings() -> dict:
        return {
            "min_disk_free_gb": 2.0,
            "max_disk_used_percent": 95.0,
            "min_memory_available_mb": 512,
            "max_load_1m": 24.0,
        }

    def test_resource_pressure_reason_uses_effective_active_worker_count(self) -> None:
        settings = {**self._pressure_settings(), "max_active_workers": 2}
        snapshot = {
            "disk_free_gb": 10.0,
            "disk_used_percent": 50.0,
            "memory_available_mb": 4096,
            "load_1m": 1.0,
            "active_worker_count": 3,
            "state_parent_writable": True,
        }
        reasons = supervisor_watchdog.resource_pressure_reasons(snapshot, settings)
        self.assertIn("active_worker_count_above_threshold", reasons)


if __name__ == "__main__":
    unittest.main()
