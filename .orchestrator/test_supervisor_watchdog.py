#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import io
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


_OLD_ENV = {}


def setUpModule() -> None:
    global _OLD_ENV
    _OLD_ENV = dict(os.environ)
    for k in list(os.environ.keys()):
        if k.startswith("PANTHEON_"):
            del os.environ[k]


def tearDownModule() -> None:
    os.environ.clear()
    os.environ.update(_OLD_ENV)


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

    def test_intentional_restart_record_is_fresh_and_pid_bound(self) -> None:
        now = datetime(2026, 7, 20, 5, 30, tzinfo=timezone.utc)
        target_sha = "a" * 40

        recorded = supervisor_watchdog.record_intentional_restart(
            self.config,
            old_pid=123,
            target_sha=target_sha,
            now=now,
        )

        self.assertEqual(recorded["old_pid"], 123)
        self.assertEqual(recorded["target_sha"], target_sha)
        self.assertEqual(
            supervisor_watchdog.load_valid_intentional_restart(
                self.config,
                now=now,
                candidate_pids={123},
            ),
            recorded,
        )
        self.assertIsNone(
            supervisor_watchdog.load_valid_intentional_restart(
                self.config,
                now=now,
                candidate_pids={999},
            )
        )
        self.assertIsNone(
            supervisor_watchdog.load_valid_intentional_restart(
                self.config,
                now=now + supervisor_watchdog.timedelta(seconds=301),
                candidate_pids={123},
            )
        )

    def test_main_prints_recorded_intent_as_json(self) -> None:
        target_sha = "a" * 40
        recorded = {
            "version": 1,
            "kind": "intentional_deploy_restart",
            "created_at": "2026-07-26T11:17:13Z",
            "expires_at": "2026-07-26T11:22:13Z",
            "old_pid": 123,
            "target_sha": target_sha,
        }
        args = mock.Mock(
            config="/tmp/watchdog-config.json",
            restart=False,
            dry_run=False,
            record_intent_pid=123,
            record_intent_target=target_sha,
            json=True,
        )

        with (
            mock.patch.object(supervisor_watchdog, "parse_args", return_value=args),
            mock.patch.object(supervisor_watchdog, "load_config", return_value=self.config),
            mock.patch.object(
                supervisor_watchdog,
                "record_intentional_restart",
                return_value=recorded,
            ) as record_intentional_restart,
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            exit_code = supervisor_watchdog.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), recorded)
        record_intentional_restart.assert_called_once_with(
            self.config,
            old_pid=123,
            target_sha=target_sha,
        )

    def test_intentional_restart_bypasses_crash_budget_and_closes_circuit(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.write_pid(123)
        self.write_state(
            {
                "supervisor": {
                    "pid": 123,
                    "last_heartbeat_at": "2026-05-18T13:00:00Z",
                    "lifecycle": "running",
                }
            }
        )
        attempts = [
            {
                "at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=offset)),
                "reason": "pid_not_alive",
            }
            for offset in (30, 60)
        ]
        (self.root / "watchdog-state.json").write_text(
            json.dumps(
                {
                    "restart_attempts": attempts,
                    "circuit": {
                        "open": True,
                        "reason": "restart_budget_window_exhausted",
                        "opened_at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=20)),
                        "until": supervisor_watchdog.isoformat_utc(now + supervisor_watchdog.timedelta(seconds=1700)),
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        supervisor_watchdog.record_intentional_restart(
            self.config,
            old_pid=123,
            target_sha="b" * 40,
            now=now,
        )
        log_path = self.root / "intentional-restart.log"

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
            mock.patch.object(supervisor_watchdog, "start_supervisor", return_value=(999, log_path)),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "restart_supervisor")
        self.assertEqual(result["reason"], "intentional_deploy_restart")
        self.assertTrue(result["intentional_restart"])
        watchdog_state = json.loads((self.root / "watchdog-state.json").read_text(encoding="utf-8"))
        self.assertEqual(watchdog_state["restart_attempts"], attempts)
        self.assertFalse(watchdog_state["circuit"]["open"])
        self.assertEqual(watchdog_state["circuit"]["previous_reason"], "restart_budget_window_exhausted")
        self.assertEqual(watchdog_state["intentional_restart_attempts"][0]["new_pid"], 999)
        self.assertEqual(watchdog_state["intentional_restart_attempts"][0]["target_sha"], "b" * 40)
        self.assertFalse(supervisor_watchdog.intentional_restart_path(self.config).exists())

    def test_resource_pressure_still_blocks_intentional_restart(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.write_pid(123)
        self.write_state(
            {
                "supervisor": {
                    "pid": 123,
                    "last_heartbeat_at": "2026-05-18T13:00:00Z",
                    "lifecycle": "running",
                }
            }
        )
        supervisor_watchdog.record_intentional_restart(
            self.config,
            old_pid=123,
            target_sha="c" * 40,
            now=now,
        )
        pressure = self.ok_resource()
        pressure["disk_free_gb"] = 0.5

        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=pressure),
            mock.patch.object(supervisor_watchdog, "start_supervisor") as start,
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "suppress_restart")
        self.assertIn("resource_pressure", result["reason"])
        self.assertFalse(result["intentional_restart"])
        start.assert_not_called()
        self.assertTrue(supervisor_watchdog.intentional_restart_path(self.config).exists())

    def test_intentional_deploy_classification_does_not_consume_crash_budget(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        attempts = [
            {
                "at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=10)),
                "reason": "pid_not_alive",
                "classification": "intentional_deploy",
            },
            {
                "at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=20)),
                "reason": "pid_not_alive",
            },
        ]

        counts = supervisor_watchdog.restart_attempt_counts(attempts, now, self.config["watchdog"])

        self.assertEqual(counts, {"window": 1, "hour": 1})

    def test_intentional_deploy_classification_does_not_trigger_backoff(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        settings = dict(self.config["watchdog"])
        settings["backoff_schedule_seconds"] = [300]
        watchdog_state = {
            "restart_attempts": [
                {
                    "at": supervisor_watchdog.isoformat_utc(now - supervisor_watchdog.timedelta(seconds=10)),
                    "reason": "pid_not_alive",
                    "classification": "intentional_deploy",
                }
            ],
            "circuit": {"open": False, "reason": None, "opened_at": None, "until": None},
        }

        reason = supervisor_watchdog.budget_suppression_reason(watchdog_state, now, settings)

        self.assertIsNone(reason)

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

    def test_missing_runtime_state_allows_guarded_first_bootstrap(self) -> None:
        log_path = self.root / "supervisor-bootstrap.log"

        with (
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
            mock.patch.object(supervisor_watchdog, "start_supervisor", return_value=(456, log_path)) as start,
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "restart_supervisor")
        self.assertEqual(result["reason"], "missing_pid")
        self.assertEqual(result["new_pid"], 456)
        start.assert_called_once()
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["watchdog"]["last_decision"], "restart_supervisor")
        self.assertEqual(state["watchdog"]["safe_mode_reason"], "missing_pid")

    def test_corrupt_runtime_state_still_suppresses_restart(self) -> None:
        self.state_file.write_text("not-json\n", encoding="utf-8")

        with (
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
            mock.patch.object(supervisor_watchdog, "start_supervisor") as start,
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "suppress_restart")
        self.assertEqual(result["reason"], "resource_pressure:state_read_failed")
        start.assert_not_called()

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
        lock_path = supervisor_watchdog.supervisor_lock_path(self.config)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
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
        self.assertEqual(captured["env"].get("PYTHONDONTWRITEBYTECODE"), "1")

    def test_supervisor_lock_held_true_when_locked(self) -> None:
        self.hold_lock()
        self.assertTrue(supervisor_watchdog.supervisor_lock_held(self.config))

    def test_supervisor_lock_held_false_when_absent_or_free(self) -> None:
        # No lock file at all.
        self.assertFalse(supervisor_watchdog.supervisor_lock_held(self.config))
        # File present but nobody holds the flock.
        lock_path = supervisor_watchdog.supervisor_lock_path(self.config)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("0\n", encoding="utf-8")
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
        import signal
        processes = []
        outputs = []
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("PANTHEON_")}
        try:
            for _ in range(12):
                p = subprocess.Popen(
                    [sys.executable, str(Path(supervisor_watchdog.__file__).resolve()), "--config", str(config_path), "--json"],
                    env=clean_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                processes.append(p)

            for p in processes:
                stdout, stderr = p.communicate(timeout=5.0)
                outputs.append((p.returncode, stdout, stderr))
        finally:
            for p in processes:
                if p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    p.wait()

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

    def test_initially_free_concurrent_probes_max_one_owner(self) -> None:
        """Proves that when the lock is initially free and multiple concurrent probes execute,
        at most one active probe can own the critical section while others immediately skip."""
        import threading
        import time

        self.write_pid(123)
        now = datetime.now(timezone.utc)
        self.write_state({
            "supervisor": {
                "pid": 123,
                "last_heartbeat_at": supervisor_watchdog.isoformat_utc(now),
                "lifecycle": "running"
            }
        })

        active_owners = 0
        max_seen_owners = 0
        owner_lock = threading.Lock()
        
        entered_event = threading.Event()
        continue_event = threading.Event()

        orig_run_locked = supervisor_watchdog._run_watchdog_locked

        def mock_run_locked(*args, **kwargs):
            nonlocal active_owners, max_seen_owners
            with owner_lock:
                active_owners += 1
                if active_owners > max_seen_owners:
                    max_seen_owners = active_owners
            
            entered_event.set()
            continue_event.wait(timeout=5.0)
            
            try:
                res = orig_run_locked(*args, **kwargs)
            finally:
                with owner_lock:
                    active_owners -= 1
            return res

        with mock.patch.object(supervisor_watchdog, "_run_watchdog_locked", side_effect=mock_run_locked), \
             mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=True), \
             mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()):
            
            results = []
            threads = []
            
            def worker():
                res = supervisor_watchdog.run_watchdog(self.config, restart=True)
                results.append(res)

            t1 = threading.Thread(target=worker)
            threads.append(t1)
            t1.start()

            self.assertTrue(entered_event.wait(timeout=5.0))

            for _ in range(4):
                t = threading.Thread(target=worker)
                threads.append(t)
                t.start()

            for t in threads[1:]:
                t.join(timeout=5.0)

            continue_event.set()
            t1.join(timeout=5.0)

            decisions = [r["decision"] for r in results]
            reasons = [r["reason"] for r in results]
            
            self.assertEqual(decisions.count("observe_only"), 1)
            self.assertEqual(decisions.count("skip"), 4)
            self.assertEqual(reasons.count("lock_contention"), 4)

            for r in results:
                if r["decision"] == "skip":
                    self.assertIsNone(r["restart_count_window"])
                    self.assertIsNone(r["restart_count_hour"])

            self.assertEqual(max_seen_owners, 1)
            self.assertEqual(active_owners, 0)

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
        import signal
        processes = []
        outputs = []
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("PANTHEON_")}
        try:
            for _ in range(5):
                p = subprocess.Popen(
                    [sys.executable, str(Path(supervisor_watchdog.__file__).resolve()), "--config", str(config_path), "--json"],
                    env=clean_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                processes.append(p)

            for p in processes:
                # Enforce a strict timeout deadline of 2.0 seconds
                stdout, stderr = p.communicate(timeout=2.0)
                outputs.append((p.returncode, stdout, stderr))
        finally:
            for p in processes:
                if p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    p.wait()

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
        sup_lock_path = supervisor_watchdog.supervisor_lock_path(self.config)
        sup_lock_path.parent.mkdir(parents=True, exist_ok=True)
        sup_lock_handle = open(sup_lock_path, "w", encoding="utf-8")
        fcntl.flock(sup_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        config_path = self.root / ".orchestrator" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f)

        import subprocess
        import signal
        health_script_path = Path(__file__).resolve().parent.parent / "scripts" / "supervisor_runtime_health.py"
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith("PANTHEON_")}
        p = subprocess.Popen(
            [sys.executable, str(health_script_path), "--repo", str(self.root), "--require-watchdog", "--json"],
            env=clean_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = p.communicate(timeout=5.0)
        finally:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.wait()

        fcntl.flock(sup_lock_handle.fileno(), fcntl.LOCK_UN)
        sup_lock_handle.close()

        self.assertEqual(p.returncode, 0, f"Health check failed with stderr: {stderr.decode()}")
        health_report = json.loads(stdout.decode())
        self.assertTrue(health_report["healthy"])

    def test_contention_metric_dropped_on_eagain(self) -> None:
        """Verify that when the contention metrics file lock raises EAGAIN, the metric write is dropped and warning is printed to stderr."""
        import io
        import errno

        orig_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            # Mock flock on the contention metrics lock descriptor to raise EAGAIN
            real_flock = fcntl.flock
            def fake_flock(fd, op):
                # When attempting LOCK_EX | LOCK_NB on the metrics-contention lock
                if op & fcntl.LOCK_NB:
                    raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
                return real_flock(fd, op)

            with mock.patch("fcntl.flock", fake_flock):
                # Call append_watchdog_contention_metric
                supervisor_watchdog.append_watchdog_contention_metric(
                    self.config,
                    {"decision": "skip", "reason": "lock_contention"},
                    self.config["watchdog"],
                )
            
            output = sys.stderr.getvalue()
            self.assertIn("watchdog contention metric write dropped due to lock contention", output)
        finally:
            sys.stderr = orig_stderr

    def test_contention_metric_raises_on_other_oserror(self) -> None:
        """Verify that when the contention metrics lock raises a non-EAGAIN OSError, it propagates."""
        import errno

        def fake_flock(fd, op):
            raise OSError(errno.EACCES, "Permission denied")

        with mock.patch("fcntl.flock", fake_flock):
            with self.assertRaises(OSError) as ctx:
                supervisor_watchdog.append_watchdog_contention_metric(
                    self.config,
                    {"decision": "skip", "reason": "lock_contention"},
                    self.config["watchdog"],
                )
            self.assertEqual(ctx.exception.errno, errno.EACCES)

    def test_watchdog_success_releases_lock_exactly_once(self) -> None:
        """Verify that on normal success, the lock is released exactly once."""
        self.write_pid(123)
        now = datetime.now(timezone.utc)
        self.write_state({
            "supervisor": {
                "pid": 123,
                "last_heartbeat_at": supervisor_watchdog.isoformat_utc(now),
                "lifecycle": "running"
            }
        })

        enter_calls = 0
        exit_calls = 0
        real_lock = supervisor_watchdog.runtime_state_lock

        class LockManagerWrapper:
            def __init__(self, target):
                self.target = target
            def __enter__(self):
                nonlocal enter_calls
                enter_calls += 1
                return self.target.__enter__()
            def __exit__(self, exc_type, exc_val, exc_tb):
                nonlocal exit_calls
                exit_calls += 1
                return self.target.__exit__(exc_type, exc_val, exc_tb)

        def fake_lock_manager(*args, **kwargs):
            if kwargs.get("nonblocking") is True:
                return LockManagerWrapper(real_lock(*args, **kwargs))
            return real_lock(*args, **kwargs)

        with (
            mock.patch.object(supervisor_watchdog, "runtime_state_lock", fake_lock_manager),
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=True),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(result["decision"], "observe_only")
        self.assertEqual(enter_calls, 1, "Lock manager enter should have been called exactly once.")
        self.assertEqual(exit_calls, 1, "Lock manager exit should have been called exactly once on success.")

    def test_contention_metric_error_surfaced_in_wrapper(self) -> None:
        """Verify that when run_watchdog hits contention and append_watchdog_contention_metric raises an exception, it is surfaced to stderr and skip is returned."""
        import io
        import errno

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

        orig_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            def fake_append(*args, **kwargs):
                raise OSError(errno.EACCES, "Permission denied")

            with (
                mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
                mock.patch.object(supervisor_watchdog, "append_watchdog_contention_metric", fake_append),
            ):
                result = supervisor_watchdog.run_watchdog(self.config, restart=True)

            self.assertEqual(result["decision"], "skip")
            self.assertEqual(result["reason"], "lock_contention")
            
            output = sys.stderr.getvalue()
            self.assertIn("watchdog contention metric write failed", output)
            self.assertIn("Permission denied", output)
        finally:
            sys.stderr = orig_stderr

    def test_non_flock_eagain_is_propagated(self) -> None:
        """Verify that a non-flock EAGAIN (e.g. from validation I/O or open) is propagated and NOT treated as lock contention."""
        import errno

        # We mock __enter__ to raise EAGAIN, but we DO NOT patch fcntl.flock to fail,
        # so flock_contention_hit remains False.
        def fake_lock_manager(*args, **kwargs):
            class BadLock:
                def __enter__(self):
                    raise OSError(errno.EAGAIN, "Non-flock EAGAIN error")
                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass
            return BadLock()

        with mock.patch.object(supervisor_watchdog, "runtime_state_lock", fake_lock_manager):
            with self.assertRaises(OSError) as ctx:
                supervisor_watchdog.run_watchdog(self.config, restart=True)
            self.assertEqual(ctx.exception.errno, errno.EAGAIN)
            self.assertIn("Non-flock EAGAIN error", str(ctx.exception))

    def test_watchdog_dry_run(self) -> None:
        """Verify that when dry_run=True is set, the decision is to restart but Popen is not called."""
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})
        
        with (
            mock.patch.object(supervisor_watchdog, "pid_is_alive", return_value=False),
            mock.patch.object(supervisor_watchdog, "resource_snapshot", return_value=self.ok_resource()),
            mock.patch.object(supervisor_watchdog.subprocess, "Popen") as mock_popen,
        ):
            result = supervisor_watchdog.run_watchdog(self.config, restart=True, dry_run=True)
            
        self.assertEqual(result["decision"], "restart_supervisor")
        self.assertIn("dry_run", result["reason"])
        mock_popen.assert_not_called()

    def test_watchdog_owner_crash_releases_lock(self) -> None:
        """Verify that when the watchdog logic raises an unexpected exception (crash), the lock is released exactly once."""
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})

        # We will mock _run_watchdog_locked to raise an unexpected Exception
        def crashing_run(*args, **kwargs):
            raise ValueError("Unexpected owner crash")

        # Let's count how many times lock_manager.__exit__ is called
        exit_calls = 0
        real_lock = supervisor_watchdog.runtime_state_lock
        
        # We wrapper the context manager to intercept exit
        class LockManagerWrapper:
            def __init__(self, target):
                self.target = target
            def __enter__(self):
                return self.target.__enter__()
            def __exit__(self, exc_type, exc_val, exc_tb):
                nonlocal exit_calls
                exit_calls += 1
                return self.target.__exit__(exc_type, exc_val, exc_tb)

        def fake_lock_manager(*args, **kwargs):
            return LockManagerWrapper(real_lock(*args, **kwargs))

        with (
            mock.patch.object(supervisor_watchdog, "runtime_state_lock", fake_lock_manager),
            mock.patch.object(supervisor_watchdog, "_run_watchdog_locked", crashing_run),
        ):
            with self.assertRaisesRegex(ValueError, "Unexpected owner crash"):
                supervisor_watchdog.run_watchdog(self.config, restart=True)

        self.assertEqual(exit_calls, 1, "Lock manager exit should have been called exactly once to clean up lock.")

    def test_watchdog_locked_body_contention_propagates(self) -> None:
        """Verify that a LockContentionError raised inside the locked body (e.g. from a nested lock attempt)
        is propagated and NOT caught or converted to a benign skip by run_watchdog."""
        import errno
        self.write_pid(123)
        self.write_state({"supervisor": {"pid": 123, "last_heartbeat_at": "2026-05-18T13:00:00Z", "lifecycle": "running"}})

        def nested_contention_run(*args, **kwargs):
            from common import LockContentionError
            import errno
            raise LockContentionError(errno.EAGAIN, "Nested lock contention", "dummy.lock")

        with mock.patch.object(supervisor_watchdog, "_run_watchdog_locked", nested_contention_run):
            from common import LockContentionError
            with self.assertRaises(LockContentionError) as ctx:
                supervisor_watchdog.run_watchdog(self.config, restart=True)
            self.assertEqual(ctx.exception.errno, errno.EAGAIN)
            self.assertIn("Nested lock contention", str(ctx.exception))

    def test_contention_metric_open_eacces_propagates(self) -> None:
        """Verify that when the metrics-lock os.open raises EACCES, the original OSError is propagated without UnboundLocalError."""
        import errno

        def fake_open(path, flags, mode=0o777):
            if str(path).endswith(".lock"):
                raise OSError(errno.EACCES, "Permission denied")
            return os_open(path, flags, mode)

        os_open = os.open
        with mock.patch("os.open", fake_open):
            with self.assertRaises(OSError) as ctx:
                supervisor_watchdog.append_watchdog_contention_metric(
                    self.config,
                    {"decision": "skip", "reason": "lock_contention"},
                    self.config["watchdog"],
                )
            self.assertEqual(ctx.exception.errno, errno.EACCES)
            self.assertNotIsInstance(ctx.exception, UnboundLocalError)

    def test_watchdog_overlap_contention_coverage(self) -> None:
        """Verify that overlapping lock attempts classify contention correctly without out-of-order fcntl.flock wrapper corruption."""
        from common import LockContentionError, canonical_task_state_lock_file
        
        lock_dir = self.root / ".orchestrator"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "runtime-admission.lock"
        raw_handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(raw_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(raw_handle.close)

        lm = supervisor_watchdog.runtime_state_lock(self.config, shared=False, nonblocking=True)
        status_file = self.root / "state.json"

        with self.assertRaises(LockContentionError):
            with lm:
                self.fail("lm shouldn't be acquired under contention")
        
        with canonical_task_state_lock_file(status_file, shared=False, nonblocking=True):
            pass

    def test_lock_contention_timeout_decouples_and_returns_fallback(self) -> None:
        """Verify that when operations in the contention path hang/timeout,
        run_watchdog returns lock_contention_timeout fallback without blocking indefinitely."""
        import time
        from common import LockContentionError

        # Set a short timeout deadline in configuration
        config = dict(self.config)
        config["watchdog"] = dict(config["watchdog"])
        config["watchdog"]["contention_deadline_seconds"] = 0.1

        # Hold the runtime lock to trigger LockContentionError
        lock_dir = self.root / ".orchestrator"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "runtime-admission.lock"
        lock_handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(lock_handle.close)

        # Mock supervisor_lock_held to block/sleep longer than the deadline
        def slow_lock_held(*args, **kwargs):
            time.sleep(0.5)
            return True

        with mock.patch.object(supervisor_watchdog, "supervisor_lock_held", slow_lock_held):
            start = time.monotonic()
            result = supervisor_watchdog.run_watchdog(config, restart=True)
            elapsed = time.monotonic() - start

        # Check that it timed out and returned within a reasonable window (less than 0.4s, since timeout is 0.1)
        self.assertLess(elapsed, 0.4)
        self.assertEqual(result["decision"], "skip")
        self.assertEqual(result["reason"], "lock_contention_timeout")
        self.assertEqual(result["resource"]["active_worker_count_source"], "skipped_due_to_timeout")
        self.assertEqual(result["resource"]["active_worker_scan_error"], "timeout")


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

    def test_harness_cleanup_leaves_no_lingering_processes(self) -> None:
        """Verify that when a process is spawned under start_new_session=True, 
        our cleanup wrapper successfully kills the process group and leaves no lingering processes."""
        import subprocess
        import signal

        # Spawn a process that runs sleep
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            start_new_session=True,
        )
        self.addCleanup(p.wait) # backup

        pid = p.pid
        # Verify it's alive
        self.assertTrue(supervisor_watchdog.pid_is_alive(pid))

        # Kill the process group
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
        p.wait()

        # Verify it is now dead
        self.assertFalse(supervisor_watchdog.pid_is_alive(pid))



class SupervisorRootCoherenceTests(unittest.TestCase):
    """SUP-PROVIDER-POOL-PROBE-GATE-001 acceptance 5.

    The live supervisor was observed running from `dev-root-6692d51c9bc5` while
    worker runners launched from `dev-root-29054ab270d5`, and the stale root was
    63 commits behind `origin/dev`. The sync/watchdog path only ever knew about
    a default `dev-root` path, so the split was invisible. Root coherence has to
    be read from the live process, not assumed from the module location.
    """

    def _write_process(
        self, proc_root: Path, pid: int, parts: list[str], cwd: Path
    ) -> None:
        proc_dir = proc_root / str(pid)
        proc_dir.mkdir(parents=True)
        (proc_dir / "cmdline").write_bytes(
            b"\x00".join(part.encode("utf-8") for part in parts) + b"\x00"
        )
        cwd.mkdir(parents=True, exist_ok=True)
        (proc_dir / "cwd").symlink_to(cwd, target_is_directory=True)

    def test_process_working_directory_reports_the_live_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            active = root / "dev-root-6692d51c9bc5"
            self._write_process(
                proc_root, 4242, ["python3", "-u", ".orchestrator/supervisor.py"], active
            )

            cwd, error = supervisor_watchdog.process_working_directory(4242, proc_root=proc_root)

        self.assertIsNone(error)
        self.assertEqual(cwd, str(active.resolve()))

    def test_process_working_directory_reports_missing_pid_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc_root = Path(tmp) / "proc"
            proc_root.mkdir()

            self.assertEqual(
                supervisor_watchdog.process_working_directory(None, proc_root=proc_root),
                (None, "no_pid"),
            )
            cwd, error = supervisor_watchdog.process_working_directory(9999, proc_root=proc_root)

        self.assertIsNone(cwd)
        self.assertTrue(str(error).startswith("cwd_unreadable:"), error)

    def test_report_exposes_a_split_between_active_and_worker_runner_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            supervisor_root = root / "dev-root-6692d51c9bc5"
            worker_root = root / "dev-root-29054ab270d5"
            (worker_root / ".orchestrator").mkdir(parents=True)
            (worker_root / ".orchestrator" / "worker_runner.py").write_text("", encoding="utf-8")

            self._write_process(
                proc_root, 100, ["python3", "-u", ".orchestrator/supervisor.py"], supervisor_root
            )
            self._write_process(
                proc_root,
                200,
                ["/usr/bin/python3", str(worker_root / ".orchestrator" / "worker_runner.py"), "--run-id", "r1"],
                worker_root,
            )

            report = supervisor_watchdog.supervisor_root_report(
                {},
                100,
                proc_root=proc_root,
                settings={"supervisor_root": str(supervisor_root)},
            )

        self.assertEqual(report["active_root"], str(supervisor_root.resolve()))
        self.assertFalse(report["split_from_expected"])
        self.assertEqual(report["worker_runner_roots"], [str(worker_root.resolve())])
        self.assertTrue(report["split_from_worker_runners"])

    def test_report_flags_an_active_root_that_is_not_the_expected_dev_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            expected_root = root / "dev-root"
            expected_root.mkdir()
            active_root = root / "dev-root-6692d51c9bc5"
            self._write_process(
                proc_root, 101, ["python3", "-u", ".orchestrator/supervisor.py"], active_root
            )

            report = supervisor_watchdog.supervisor_root_report(
                {},
                101,
                proc_root=proc_root,
                settings={"supervisor_root": str(expected_root)},
            )

        self.assertEqual(report["expected_root"], str(expected_root.resolve()))
        self.assertEqual(report["active_root"], str(active_root.resolve()))
        self.assertTrue(report["split_from_expected"])
        # No live worker runners is not a split.
        self.assertEqual(report["worker_runner_roots"], [])
        self.assertFalse(report["split_from_worker_runners"])

    def test_watchdog_decision_publishes_the_active_supervisor_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "paths": {
                    "state_file": str(root / "state.json"),
                    "activity_log": str(root / "activity-log.jsonl"),
                },
                "watchdog": {
                    "enabled": False,
                    "state_file": str(root / "watchdog-state.json"),
                    "metrics_file": str(root / "metrics.jsonl"),
                    "contention_metrics_file": str(root / "metrics-contention.jsonl"),
                },
            }
            (root / "state.json").write_text(json.dumps({"workers": {}}), encoding="utf-8")
            observed: dict = {}

            def fake_root_report(_config, pid, **_kwargs):
                observed["pid"] = pid
                return {"expected_root": "/expected", "active_root": "/actual", "split_from_expected": True}

            with mock.patch.object(
                supervisor_watchdog, "supervisor_root_report", side_effect=fake_root_report
            ):
                result = supervisor_watchdog.run_watchdog(config)

        self.assertEqual(result["supervisor_root"]["active_root"], "/actual")
        self.assertTrue(result["supervisor_root"]["split_from_expected"])
        self.assertIn("pid", observed)


if __name__ == "__main__":
    unittest.main()
