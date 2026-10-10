"""Tests for scheduled reconciliation incident causal telemetry linking and canonical lineage.

Verifies:
- _scheduled_drift_report derives exactly one deterministic causal telemetry event ID
  from genuine stored owner evidence (last_heartbeat_event_id for runtime health,
  last_event_id for execution drift).
- The drift report output satisfies build_incident_from_drift_report cardinality rule
  (len(telemetry_event_ids) == 1) without IncidentConsumerError.
- The evaluation record retains full provenance with all telemetry events in evidence_refs.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest import mock

from services.incidents.consumer import (
    IncidentConsumerError,
    build_incident_from_drift_report,
)

SERVICE_DIR = Path(__file__).resolve().parent


def _load_main_module():
    with mock.patch.dict(
        "os.environ",
        {
            "RECONCILIATION_DRIFT_DATA_DIR": tempfile.mkdtemp(),
            "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
            "PANTHEON_LINEAGE_READ_URL": "http://lineage-read:8094",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location(
                "reconciliation_drift_causal_test_main",
                SERVICE_DIR / "main.py",
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["reconciliation_drift_causal_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


_main = _load_main_module()
_scheduled_drift_report = _main._scheduled_drift_report
_summary_telemetry_event_ids = _main._summary_telemetry_event_ids


class TestScheduledDriftReportCausalLineage(unittest.TestCase):
    def setUp(self) -> None:
        self.summary_base: Dict[str, Any] = {
            "binding_id": "bind-sched-001",
            "runtime_id": "rt-sched-001",
            "deployment_stage": "live",
            "deployment_plan_id": "plan-sched-001",
            "capital_pool_id": "pool-sched-001",
            "persona_capital_binding_id": "pcb-sched-001",
            "artifact_id": "art-sched-001",
            "artifact_version": "1.0.0",
            "trace_id": "trace-sched-001",
            "baseline_ref": "governed-runtime-health-policy",
            "telemetry_event_ids": ["evt-base-001", "evt-base-002"],
            "last_event_id": "evt-execution-trade-099",
            "last_heartbeat_event_id": "evt-runtime-hb-088",
        }

    def test_summary_telemetry_event_ids_aggregates_all_events(self) -> None:
        all_ids = _summary_telemetry_event_ids(self.summary_base)
        self.assertIn("evt-base-001", all_ids)
        self.assertIn("evt-base-002", all_ids)
        self.assertIn("evt-execution-trade-099", all_ids)
        self.assertIn("evt-runtime-hb-088", all_ids)
        self.assertEqual(len(all_ids), 4)

    def test_scheduled_drift_report_runtime_health_selects_heartbeat(self) -> None:
        all_ids = _summary_telemetry_event_ids(self.summary_base)
        evaluation: Dict[str, Any] = {
            "evaluation_id": "eval-sched-001",
            "binding_id": "bind-sched-001",
            "runtime_id": "rt-sched-001",
            "tenant_id": "default",
            "reconciliation_checks": [
                {
                    "check": "runtime_health_summary",
                    "status": "warning",
                    "detail": "queue lag breach",
                    "metric": "queue_lag_ms",
                }
            ],
            "drift_checks": [],
            "evidence_refs": [
                {"type": "telemetry_event", "id": event_id}
                for event_id in all_ids
            ],
        }

        report = _scheduled_drift_report(
            summary=self.summary_base,
            evaluation=evaluation,
            telemetry_event_ids=all_ids,
            timestamp="2026-10-10T10:00:00Z",
        )

        self.assertIsNotNone(report)
        assert report is not None

        # AC2: report links exactly one causal telemetry event ID matching the heartbeat
        self.assertEqual(report["telemetry_event_ids"], ["evt-runtime-hb-088"])
        self.assertEqual(report["drift_type"], "runtime_health")

        # Evidence refs must contain exactly one telemetry_event ref
        telemetry_refs = [
            ref for ref in report["evidence_refs"]
            if str(ref).startswith("telemetry_event:")
        ]
        self.assertEqual(telemetry_refs, ["telemetry_event:evt-runtime-hb-088"])

        # Retains full evaluation provenance
        self.assertEqual(len(evaluation["evidence_refs"]), 4)

        # Must cleanly consume into IncidentCase without IncidentConsumerError
        incident = build_incident_from_drift_report(report)
        self.assertEqual(incident.telemetry_event_ids, ["evt-runtime-hb-088"])
        self.assertEqual(incident.binding_id, "bind-sched-001")
        self.assertEqual(incident.severity, "medium")

    def test_scheduled_drift_report_execution_drift_selects_last_event(self) -> None:
        all_ids = _summary_telemetry_event_ids(self.summary_base)
        evaluation: Dict[str, Any] = {
            "evaluation_id": "eval-sched-002",
            "binding_id": "bind-sched-001",
            "runtime_id": "rt-sched-001",
            "tenant_id": "default",
            "reconciliation_checks": [],
            "drift_checks": [
                {
                    "metric": "fill_rate",
                    "status": "critical",
                    "observed": 0.42,
                    "baseline": 0.95,
                }
            ],
            "evidence_refs": [
                {"type": "telemetry_event", "id": event_id}
                for event_id in all_ids
            ],
        }

        report = _scheduled_drift_report(
            summary=self.summary_base,
            evaluation=evaluation,
            telemetry_event_ids=all_ids,
            timestamp="2026-10-10T10:00:00Z",
        )

        self.assertIsNotNone(report)
        assert report is not None

        # AC2: report links exactly one causal telemetry event ID matching execution last_event_id
        self.assertEqual(report["telemetry_event_ids"], ["evt-execution-trade-099"])
        self.assertEqual(report["drift_type"], "execution")

        telemetry_refs = [
            ref for ref in report["evidence_refs"]
            if str(ref).startswith("telemetry_event:")
        ]
        self.assertEqual(telemetry_refs, ["telemetry_event:evt-execution-trade-099"])

        # Must cleanly consume into IncidentCase without IncidentConsumerError
        incident = build_incident_from_drift_report(report)
        self.assertEqual(incident.telemetry_event_ids, ["evt-execution-trade-099"])
        self.assertEqual(incident.severity, "critical")

    def test_consumer_rejects_report_with_multiple_telemetry_events(self) -> None:
        # Reproduce original failure: if report had multiple telemetry events
        bad_report = {
            "drift_report_id": "drift-bad-001",
            "binding_id": "bind-sched-001",
            "runtime_id": "rt-sched-001",
            "deployment_stage": "live",
            "deployment_plan_id": "plan-sched-001",
            "capital_pool_id": "pool-sched-001",
            "persona_capital_binding_id": "pcb-sched-001",
            "artifact_id": "art-sched-001",
            "artifact_version": "1.0.0",
            "trace_id": "trace-sched-001",
            "telemetry_event_ids": ["evt-1", "evt-2"],
            "severity": "high",
        }
        with self.assertRaises(IncidentConsumerError) as ctx:
            build_incident_from_drift_report(bad_report)
        self.assertIn("drift report must link exactly one telemetry_event_id", str(ctx.exception))

    def test_scheduled_drift_report_fails_closed_when_causal_anchor_missing_in_multi_event(self) -> None:
        summary = dict(self.summary_base)
        summary["last_heartbeat_event_id"] = None
        evaluation: Dict[str, Any] = {
            "evaluation_id": "eval-sched-003",
            "binding_id": "bind-sched-001",
            "runtime_id": "rt-sched-001",
            "tenant_id": "default",
            "reconciliation_checks": [
                {"check": "runtime_health_summary", "status": "warning", "metric": "queue_lag_ms"}
            ],
            "drift_checks": [],
        }
        report = _scheduled_drift_report(
            summary=summary,
            evaluation=evaluation,
            telemetry_event_ids=["evt-base-001", "evt-base-002"],
            timestamp="2026-10-10T10:00:00Z",
        )
        self.assertIsNone(report)

    def test_scheduled_drift_report_fails_closed_when_causal_anchor_not_in_telemetry_events(self) -> None:
        summary = dict(self.summary_base)
        summary["last_heartbeat_event_id"] = "evt-foreign-unobserved"
        evaluation: Dict[str, Any] = {
            "evaluation_id": "eval-sched-004",
            "binding_id": "bind-sched-001",
            "runtime_id": "rt-sched-001",
            "tenant_id": "default",
            "reconciliation_checks": [
                {"check": "runtime_health_summary", "status": "warning", "metric": "queue_lag_ms"}
            ],
            "drift_checks": [],
        }
        report = _scheduled_drift_report(
            summary=summary,
            evaluation=evaluation,
            telemetry_event_ids=["evt-base-001", "evt-base-002"],
            timestamp="2026-10-10T10:00:00Z",
        )
        self.assertIsNone(report)

    def test_scheduled_drift_report_legacy_single_event_succeeds(self) -> None:
        summary = dict(self.summary_base)
        summary["last_heartbeat_event_id"] = None
        summary["last_event_id"] = None
        evaluation: Dict[str, Any] = {
            "evaluation_id": "eval-sched-005",
            "binding_id": "bind-sched-001",
            "runtime_id": "rt-sched-001",
            "tenant_id": "default",
            "reconciliation_checks": [
                {"check": "runtime_health_summary", "status": "warning", "metric": "queue_lag_ms"}
            ],
            "drift_checks": [],
        }
        report = _scheduled_drift_report(
            summary=summary,
            evaluation=evaluation,
            telemetry_event_ids=["evt-legacy-only-001"],
            timestamp="2026-10-10T10:00:00Z",
        )
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report["telemetry_event_ids"], ["evt-legacy-only-001"])
        self.assertEqual(report["evidence_refs"][0], "telemetry_event:evt-legacy-only-001")

    def test_scheduled_drift_report_no_cross_anchor_fallback(self) -> None:
        # Health check with execution event present but no heartbeat event must not cross-fall back
        summary = dict(self.summary_base)
        summary["last_heartbeat_event_id"] = None
        summary["last_event_id"] = "evt-trade-only"
        evaluation: Dict[str, Any] = {
            "evaluation_id": "eval-sched-006",
            "binding_id": "bind-sched-001",
            "runtime_id": "rt-sched-001",
            "tenant_id": "default",
            "reconciliation_checks": [
                {"check": "runtime_health_summary", "status": "warning", "metric": "queue_lag_ms"}
            ],
            "drift_checks": [],
        }
        report = _scheduled_drift_report(
            summary=summary,
            evaluation=evaluation,
            telemetry_event_ids=["evt-trade-only", "evt-other"],
            timestamp="2026-10-10T10:00:00Z",
        )
        self.assertIsNone(report)


if __name__ == "__main__":
    unittest.main()
