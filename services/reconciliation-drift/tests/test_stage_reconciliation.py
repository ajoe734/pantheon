"""Tests for canary and live reconciliation endpoints with drift alert and incident/evolution handoff."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]

_MODULE_NAME = "reconciliation_drift_stage_test_main"


def _load_module(data_dir: str):
    """Load service module registered in sys.modules so Pydantic TypeAdapters resolve."""
    sys.modules.pop("store", None)
    sys.modules.pop(_MODULE_NAME, None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, SERVICE_DIR / "main.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        with mock.patch.dict("os.environ", {
            "RECONCILIATION_DRIFT_DATA_DIR": data_dir,
            "RECONCILIATION_DRIFT_STORE_BACKEND": "json",
            "PERSISTENCE_POSTURE": "lenient",
        }):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("store", None)


def _make_telemetry_event(
    *,
    binding_id: str = "rtb-001",
    deployment_stage: str = "canary",
    deployment_plan_id: str = "plan-001",
    artifact_id: str = "art-001",
    capital_pool_id: str = "pool-001",
    pnl: float = 1000.0,
    drawdown: float = 0.05,
    slippage: float = 2.0,
    event_id: str = "evt-001",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "heartbeat",
        "binding_id": binding_id,
        "deployment_stage": deployment_stage,
        "deployment_plan_id": deployment_plan_id,
        "artifact_id": artifact_id,
        "capital_pool_id": capital_pool_id,
        "metrics": {
            "pnl": pnl,
            "drawdown": drawdown,
            "avg_slippage_bps": slippage,
        },
    }


class CanaryReconciliationTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._mod = _load_module(self._tmpdir.name)
        self.client = TestClient(self._mod.app)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_canary_reconciliation_clean_metrics_resolves(self):
        body = {
            "binding_id": "rtb-can-001",
            "runtime_id": "rt-can-001",
            "deployment_plan_id": "plan-001",
            "artifact_id": "art-001",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-001",
            "persona_capital_binding_id": "pcb-001",
            "trace_id": "trace-can-001",
            "telemetry_events": [
                _make_telemetry_event(
                    binding_id="rtb-can-001",
                    deployment_stage="canary",
                    deployment_plan_id="plan-001",
                    artifact_id="art-001",
                    capital_pool_id="pool-001",
                    pnl=1000.0,
                    drawdown=0.05,
                )
            ],
            "baseline_metrics": {"pnl": 1000.0, "drawdown": 0.05},
            "thresholds": {},
        }
        resp = self.client.post("/api/reconciliation-drift/canary-runs/reconcile", json=body)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        record = data["record"]
        self.assertEqual(record["deployment_stage"], "canary")
        self.assertEqual(record["recon_type"], "canary_run")
        self.assertEqual(record["status"], "resolved")
        self.assertIsNone(data["incident_request"])
        self.assertIsNone(data["evolution_proposal"])

    def test_canary_drift_breach_produces_incident_and_evolution_proposal(self):
        body = {
            "binding_id": "rtb-can-002",
            "runtime_id": "rt-can-002",
            "deployment_plan_id": "plan-001",
            "artifact_id": "art-001",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-001",
            "persona_capital_binding_id": "pcb-001",
            "trace_id": "trace-can-002",
            "telemetry_events": [
                _make_telemetry_event(
                    binding_id="rtb-can-002",
                    deployment_stage="canary",
                    deployment_plan_id="plan-001",
                    artifact_id="art-001",
                    capital_pool_id="pool-001",
                    pnl=200.0,  # 80% drift from baseline 1000 → critical
                    drawdown=0.15,  # 3x baseline → critical
                )
            ],
            "baseline_metrics": {"pnl": 1000.0, "drawdown": 0.05},
            "thresholds": {},
            "create_incident_on_breach": True,
            "propose_evolution_on_breach": True,
        }
        resp = self.client.post("/api/reconciliation-drift/canary-runs/reconcile", json=body)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        record = data["record"]
        self.assertEqual(record["deployment_stage"], "canary")
        self.assertEqual(record["status"], "open")
        # incident request produced (no external API URL → incident_case=None)
        incident_req = data["incident_request"]
        self.assertIsNotNone(incident_req)
        self.assertEqual(incident_req["deployment_stage"], "canary")
        self.assertIn("canary", incident_req["title"].lower())
        # evolution proposal produced with proposed-only semantics
        evo = data["evolution_proposal"]
        self.assertIsNotNone(evo)
        self.assertEqual(evo["decision_state"], "proposed")
        self.assertEqual(evo["target_stage"], "canary")
        self.assertTrue(evo["metadata"]["proposed_only"])
        self.assertFalse(evo["metadata"]["automatic_execution_allowed"])

    def test_canary_stage_mismatch_in_telemetry_produces_critical_check(self):
        body = {
            "binding_id": "rtb-can-003",
            "runtime_id": "rt-can-003",
            "deployment_plan_id": "plan-001",
            "artifact_id": "art-001",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-001",
            "persona_capital_binding_id": "pcb-001",
            "trace_id": "trace-can-003",
            "telemetry_events": [
                _make_telemetry_event(
                    binding_id="rtb-can-003",
                    deployment_stage="paper",  # wrong stage for canary endpoint
                    deployment_plan_id="plan-001",
                    artifact_id="art-001",
                    capital_pool_id="pool-001",
                    pnl=1000.0,
                )
            ],
            "baseline_metrics": {"pnl": 1000.0},
            "thresholds": {},
        }
        resp = self.client.post("/api/reconciliation-drift/canary-runs/reconcile", json=body)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        record = data["record"]
        recon_checks = record["delta_summary"]["reconciliation_checks"]
        stage_check = next(
            (c for c in recon_checks if c["check"] == "telemetry_deployment_stage_alignment"),
            None,
        )
        self.assertIsNotNone(stage_check)
        self.assertEqual(stage_check["status"], "critical")


class LiveReconciliationTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._mod = _load_module(self._tmpdir.name)
        self.client = TestClient(self._mod.app)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_live_reconciliation_clean_metrics_resolves(self):
        body = {
            "binding_id": "rtb-live-001",
            "runtime_id": "rt-live-001",
            "deployment_plan_id": "plan-001",
            "artifact_id": "art-001",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-001",
            "persona_capital_binding_id": "pcb-001",
            "trace_id": "trace-live-001",
            "telemetry_events": [
                _make_telemetry_event(
                    binding_id="rtb-live-001",
                    deployment_stage="live",
                    deployment_plan_id="plan-001",
                    artifact_id="art-001",
                    capital_pool_id="pool-001",
                    pnl=1200.0,
                    drawdown=0.04,
                )
            ],
            "baseline_metrics": {"pnl": 1200.0, "drawdown": 0.04},
            "thresholds": {},
        }
        resp = self.client.post("/api/reconciliation-drift/live-runs/reconcile", json=body)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        record = data["record"]
        self.assertEqual(record["deployment_stage"], "live")
        self.assertEqual(record["recon_type"], "live_run")
        self.assertEqual(record["status"], "resolved")
        self.assertIsNone(data["incident_request"])
        self.assertIsNone(data["evolution_proposal"])

    def test_live_drift_breach_produces_proposed_only_evolution(self):
        body = {
            "binding_id": "rtb-live-002",
            "runtime_id": "rt-live-002",
            "deployment_plan_id": "plan-001",
            "artifact_id": "art-001",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-001",
            "persona_capital_binding_id": "pcb-001",
            "trace_id": "trace-live-002",
            "telemetry_events": [
                _make_telemetry_event(
                    binding_id="rtb-live-002",
                    deployment_stage="live",
                    deployment_plan_id="plan-001",
                    artifact_id="art-001",
                    capital_pool_id="pool-001",
                    pnl=100.0,  # 90% drop from 1000 baseline → critical
                )
            ],
            "baseline_metrics": {"pnl": 1000.0},
            "thresholds": {},
            "create_incident_on_breach": True,
            "propose_evolution_on_breach": True,
        }
        resp = self.client.post("/api/reconciliation-drift/live-runs/reconcile", json=body)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        record = data["record"]
        self.assertEqual(record["deployment_stage"], "live")
        self.assertEqual(record["status"], "open")
        incident_req = data["incident_request"]
        self.assertIsNotNone(incident_req)
        self.assertEqual(incident_req["deployment_stage"], "live")
        evo = data["evolution_proposal"]
        self.assertIsNotNone(evo)
        self.assertEqual(evo["target_stage"], "live")
        self.assertTrue(evo["metadata"]["proposed_only"])
        self.assertFalse(evo["metadata"]["automatic_execution_allowed"])

    def test_live_missing_telemetry_degrades_reconciliation(self):
        body = {
            "binding_id": "rtb-live-003",
            "runtime_id": "rt-live-003",
            "deployment_plan_id": "plan-001",
            "artifact_id": "art-001",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-001",
            "persona_capital_binding_id": "pcb-001",
            "trace_id": "trace-live-003",
            "telemetry_events": [],
            "baseline_metrics": {"pnl": 1000.0},
            "thresholds": {},
        }
        resp = self.client.post("/api/reconciliation-drift/live-runs/reconcile", json=body)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        record = data["record"]
        self.assertEqual(record["status"], "open")
        recon_checks = record["delta_summary"]["reconciliation_checks"]
        tel_check = next((c for c in recon_checks if c["check"] == "telemetry_presence"), None)
        self.assertIsNotNone(tel_check)
        self.assertEqual(tel_check["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
