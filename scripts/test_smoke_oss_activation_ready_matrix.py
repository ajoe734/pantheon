#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

import smoke_oss_activation_ready_matrix as matrix


ROOT = Path(__file__).resolve().parents[1]


class OssActivationReadySmokeMatrixTest(unittest.TestCase):
    def test_matrix_runs_default_closed_and_enabled_offline_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = matrix.run_matrix(Path(tmp))

            self.assertTrue(report["summary"]["matrix_passed"])
            self.assertEqual(report["summary"]["forbidden_writes"]["registry_write"], False)
            self.assertEqual(report["summary"]["forbidden_writes"]["governance_write"], False)
            self.assertEqual(report["summary"]["forbidden_writes"]["broker_write"], False)
            self.assertEqual(report["summary"]["forbidden_writes"]["live_write"], False)

            rows = {row["row"]: row for row in report["rows"]}
            for worker in ("qlib", "trl", "finrl", "rllib", "wandb"):
                self.assertEqual(rows[f"default:{worker}:offline"]["status"], "passed")
                self.assertEqual(
                    rows[f"default:{worker}:offline"]["evidence"]["rejection_reason"],
                    "dispatch_mode_disabled",
                )

            for worker in ("qlib", "trl", "finrl", "rllib"):
                row = rows[f"enabled:{worker}:offline"]
                self.assertEqual(row["status"], "passed")
                self.assertEqual(row["evidence"]["artifact_state"], "draft")
                self.assertEqual(row["evidence"]["deployment_stage"], "none")
                for artifact_path in row["evidence"]["artifact_paths"]:
                    self.assertTrue(Path(artifact_path).exists())
                    Path(artifact_path).resolve().relative_to(Path(tmp).resolve())

            wandb = rows["enabled:wandb:offline-local"]
            self.assertEqual(wandb["status"], "passed")
            self.assertEqual(wandb["evidence"]["sync_status"], "offline_local")
            self.assertEqual(wandb["evidence"]["online_sync_enabled"], False)
            self.assertEqual(wandb["evidence"]["deployment_stage"], "none")

            for mode in ("paper", "canary", "live"):
                self.assertEqual(rows[f"default:{mode}"]["evidence"]["rejection_reason"], "production_adapter_disabled")
                self.assertEqual(rows[f"enabled:{mode}"]["evidence"]["rejection_reason"], "production_adapter_disabled")

    def test_cli_writes_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            exit_code = matrix.main(["--output-dir", str(Path(tmp) / "out"), "--json-out", str(out)])

            self.assertEqual(exit_code, 0)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(report["summary"]["matrix_passed"])
            self.assertEqual(report["task_id"], matrix.TASK_ID)

    def test_compose_documents_activation_ready_smoke_profile(self) -> None:
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        service = compose["services"]["oss-activation-ready-smoke-matrix"]

        self.assertEqual(service["profiles"], ["activation-ready-smoke"])
        self.assertEqual(service["command"], ["python", "scripts/smoke_oss_activation_ready_matrix.py"])
        self.assertEqual(service["environment"]["PANTHEON_OFFLINE_GATE_ENABLED"], "false")
        self.assertEqual(service["environment"]["RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS"], "false")
        self.assertNotIn("ports", service)


if __name__ == "__main__":
    unittest.main()
