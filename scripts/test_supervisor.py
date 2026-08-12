#!/usr/bin/env python3
"""Small command-line smoke suite for Supervisor Authority V2.

Deep planner, queue, recovery, lease, and fault contracts live in
``.orchestrator/test_supervisor.py``.  This entrypoint keeps the historical
``python3 scripts/test_supervisor.py`` command useful without duplicating the
retired failure-streak/reassignment control plane.
"""
from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ORCH_DIR = ROOT / ".orchestrator"
sys.path.insert(0, str(ORCH_DIR))

import supervisor
import runtime_state


class SupervisorV2SmokeTests(unittest.TestCase):
    def test_repo_configs_use_adaptive_stall_detection(self) -> None:
        for relative_path in (
            ".orchestrator/config.json",
            ".orchestrator/config.example.json",
        ):
            config = json.loads((ROOT / relative_path).read_text())
            supervisor_config = config["supervisor"]
            self.assertEqual(int(supervisor_config["stall_after_seconds"]), 300)
            self.assertIs(supervisor_config["adaptive_stall_detection"], True)

    def test_repo_config_passes_strict_account_schema(self) -> None:
        config = json.loads((ORCH_DIR / "config.json").read_text())
        supervisor.validate_provider_accounts(config)

    def test_terminal_quota_classification_does_not_mutate_assignment(self) -> None:
        config = json.loads((ORCH_DIR / "config.json").read_text())
        worker = {"provider": "codex", "task_id": "TASK-1"}
        classified = supervisor.classify_worker_failure(
            config,
            worker,
            "402 You have no quota",
        )
        self.assertEqual(classified["kind"], "quota_terminal")
        self.assertNotIn("reassigned_to", worker)

    def test_bootstrap_publishes_lifecycle_only(self) -> None:
        config = json.loads((ORCH_DIR / "config.json").read_text())
        state = runtime_state.default_state()
        supervisor.stamp_supervisor_runtime_state(
            config,
            state,
            heartbeat_at="2026-08-11T00:00:00Z",
            lifecycle="starting",
            loop_started_at="2026-08-11T00:00:00Z",
        )
        runtime = state["supervisor"]
        self.assertEqual(runtime["lifecycle"], "starting")
        self.assertNotIn("focus_mode", runtime)
        self.assertNotIn("mode_occupancy", runtime)

    def test_retry_and_recovery_cannot_launch_workers(self) -> None:
        retry_source = inspect.getsource(supervisor.retry_due_workers)
        recovery_source = inspect.getsource(
            supervisor.reconcile_unavailable_assignments
        )
        self.assertNotIn("start_worker_for_request", retry_source)
        self.assertNotIn("start_worker_for_request", recovery_source)


if __name__ == "__main__":
    unittest.main()
