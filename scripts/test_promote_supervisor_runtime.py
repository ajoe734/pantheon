#!/usr/bin/env python3
"""Comprehensive test suite for promote_supervisor_runtime.py and failure injection scenarios."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / ".orchestrator") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))

import promote_supervisor_runtime
from promote_supervisor_runtime import PromotionTransaction


class PromoteSupervisorRuntimeUnitTests(unittest.TestCase):
    def test_verify_target_root_rejects_dirty_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            target = tmp_path / "target"
            target.mkdir()

            config_path = tmp_path / "config.json"
            config_path.write_text(json.dumps({"paths": {}}), encoding="utf-8")

            evidence_dir = tmp_path / "evidence"
            evidence_dir.mkdir()

            tx = PromotionTransaction(config_path, target, evidence_dir)
            with self.assertRaises(ValueError):
                tx.verify_target_root()

            (target / ".orchestrator").mkdir()
            (target / ".orchestrator" / "supervisor.py").write_text("print(1)")

            with self.assertRaises(ValueError):
                tx.verify_target_root()

    def test_verify_supervisor_state_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config_path = tmp_path / "config.json"
            config_path.write_text(json.dumps({"paths": {}}), encoding="utf-8")
            evidence_dir = tmp_path / "evidence"
            evidence_dir.mkdir()

            tx = PromotionTransaction(config_path, tmp_path, evidence_dir)

            valid_state = {"supervisor": {"lifecycle": "ok", "task_state_shadow": "abc"}, "workers": {}}
            valid_status = {"tasks": []}
            tx.verify_supervisor_state_invariants(valid_state, valid_status)

            degraded_state = {"supervisor": {"lifecycle": "degraded", "task_state_shadow": "abc"}}
            with self.assertRaises(ValueError):
                tx.verify_supervisor_state_invariants(degraded_state, valid_status)

            missing_shadow_state = {"supervisor": {"lifecycle": "ok"}}
            with self.assertRaises(ValueError):
                tx.verify_supervisor_state_invariants(missing_shadow_state, valid_status)

            dup_task_status = {
                "tasks": [
                    {"id": "T1", "status": "in_progress"},
                    {"id": "T1", "status": "in_progress"},
                ]
            }
            with self.assertRaises(ValueError):
                tx.verify_supervisor_state_invariants(valid_state, dup_task_status)


class PromoteSupervisorRuntimeFailureInjectionTests(unittest.TestCase):
    def test_failure_injection_triggers_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            target = tmp_path / "target"
            target.mkdir()

            config_path = tmp_path / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "paths": {
                            "state_file": str(tmp_path / "state.json"),
                            "status_file": str(tmp_path / "status.json"),
                            "activity_log": str(tmp_path / "activity.jsonl"),
                        }
                    }
                ),
                encoding="utf-8",
            )
            evidence_dir = tmp_path / "evidence"
            evidence_dir.mkdir()

            (tmp_path / "state.json").write_text(
                json.dumps({"supervisor": {"lifecycle": "ok", "task_state_shadow": "hash"}}),
                encoding="utf-8",
            )
            (tmp_path / "status.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
            (tmp_path / "activity.jsonl").write_text("", encoding="utf-8")

            def failure_injector(point: str, tx: PromotionTransaction) -> None:
                if point == "after_launch_new":
                    raise RuntimeError("Simulated launch failure")

            tx = PromotionTransaction(
                config_path,
                target,
                evidence_dir,
                failure_injector=failure_injector,
            )

            with mock.patch.object(tx, "verify_target_root", return_value="target_sha_123"):
                with mock.patch.object(
                    tx, "inspect_live_supervisor", return_value=(12345, tmp_path, "initial_sha_123")
                ):
                    with mock.patch.object(tx, "stop_supervisor_under_lock"):
                        with mock.patch.object(tx, "launch_supervisor", return_value=(54321, tmp_path / "log.txt")):
                            with mock.patch.object(tx, "execute_rollback") as mock_rollback:
                                res = tx.run()

            self.assertEqual(res["status"], "rolled_back")
            self.assertIn("Simulated launch failure", res["reason"])
            mock_rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
