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
_accepted_append_visibility_reason = _main._accepted_append_visibility_reason


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


class TestAcceptedAppendVisibilityReason(unittest.TestCase):
    def setUp(self) -> None:
        self.binding_id = "rb-test-001"
        self.accepted_event_id = "evt-accepted-001"
        self.observed_event_id = "evt-observed-002"
        self.telemetry_url = "http://telemetry.local:8083"
        self.timestamp = "2026-10-11T12:00:00Z"

        self.accepted_evaluation = {
            "evaluation_id": "eval-accepted-001",
            "binding_id": self.binding_id,
            "evaluated_at": "2026-10-08T10:00:00Z",
            "lifecycle_append": {
                "status": "accepted",
                "event_id": self.accepted_event_id,
                "accepted_at": "2026-10-08T10:00:00Z",
                "summary_visibility_confirmed_at": "2026-10-08T10:01:00Z",
                "event": {
                    "event_id": self.accepted_event_id,
                    "aggregate_type": "journey",
                    "aggregate_id": "j-old",
                    "sequence_no": 5,
                },
            },
        }

        # Bounded 512 window with older accepted_event_id evicted
        self.evicted_recent_ids = [f"evt-window-{i:03d}" for i in range(512)]
        self.evicted_recent_ids[-1] = self.observed_event_id

        self.summary_evicted = {
            "binding_id": self.binding_id,
            "last_lifecycle_identity": {
                "event_id": self.observed_event_id,
                "aggregate_type": "journey",
                "aggregate_id": "j-new",
                "sequence_no": 1,
            },
            "recent_lifecycle_event_ids": self.evicted_recent_ids,
        }

    def test_evicted_but_confirmed_accepted_append_recovers_visibility_via_durable_order(self) -> None:
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            mock_verify.return_value = (
                True,
                None,
                {
                    "accepted_event_id": self.accepted_event_id,
                    "observed_event_id": self.observed_event_id,
                    "binding_id": self.binding_id,
                    "accepted_ingested_seq": 1000,
                    "observed_ingested_seq": 2000,
                },
            )
            reason, visibility = _accepted_append_visibility_reason(
                summary=self.summary_evicted,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertIsNone(reason)
            self.assertEqual(visibility["waiting_for_event_id"], self.accepted_event_id)
            self.assertEqual(visibility["observed_event_id"], self.observed_event_id)
            mock_verify.assert_called_once()

    def test_evicted_confirmed_fails_closed_when_telemetry_order_out_of_order(self) -> None:
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            mock_verify.return_value = (False, "out_of_order", {})
            reason, visibility = _accepted_append_visibility_reason(
                summary=self.summary_evicted,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")

    def test_evicted_confirmed_fails_closed_when_event_not_found(self) -> None:
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            mock_verify.return_value = (False, "event_not_found", {})
            reason, visibility = _accepted_append_visibility_reason(
                summary=self.summary_evicted,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")

    def test_evicted_confirmed_fails_closed_when_equal_sequence(self) -> None:
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            mock_verify.return_value = (False, "equal_sequence", {})
            reason, visibility = _accepted_append_visibility_reason(
                summary=self.summary_evicted,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")

    def test_evicted_confirmed_fails_closed_when_foreign_tenant_or_runtime_or_binding(self) -> None:
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            mock_verify.return_value = (False, "mismatched_runtime", {})
            reason, visibility = _accepted_append_visibility_reason(
                summary=self.summary_evicted,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")

    def test_evicted_confirmed_fails_closed_when_telemetry_unavailable(self) -> None:
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            from telemetry_client import TelemetryUnavailable
            mock_verify.side_effect = TelemetryUnavailable("connection refused")
            reason, visibility = _accepted_append_visibility_reason(
                summary=self.summary_evicted,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")

    def test_evicted_confirmed_fails_closed_without_telemetry_url(self) -> None:
        reason, visibility = _accepted_append_visibility_reason(
            summary=self.summary_evicted,
            binding_id=self.binding_id,
            timestamp=self.timestamp,
            evaluations=[self.accepted_evaluation],
            telemetry_url=None,
        )
        self.assertEqual(reason, "accepted_lifecycle_append_not_visible")

    def test_evicted_unconfirmed_fails_closed_without_calling_durable_order(self) -> None:
        # If prior accepted append was never visibility confirmed, durable order cannot discharge barrier
        unconfirmed_eval = dict(self.accepted_evaluation)
        unconfirmed_eval["lifecycle_append"] = dict(self.accepted_evaluation["lifecycle_append"])
        unconfirmed_eval["lifecycle_append"].pop("summary_visibility_confirmed_at", None)

        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            reason, visibility = _accepted_append_visibility_reason(
                summary=self.summary_evicted,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[unconfirmed_eval],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
            mock_verify.assert_not_called()

    def test_in_window_ordered_after_does_not_need_telemetry_durable_call(self) -> None:
        # In-window ordered after accepted: both are in recent_lifecycle_event_ids
        recent = [self.accepted_event_id, "evt-mid-001", self.observed_event_id]
        summary_in_window = {
            "binding_id": self.binding_id,
            "last_lifecycle_identity": {
                "event_id": self.observed_event_id,
                "aggregate_type": "journey",
                "aggregate_id": "j-new",
                "sequence_no": 1,
            },
            "recent_lifecycle_event_ids": recent,
        }
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            reason, visibility = _accepted_append_visibility_reason(
                summary=summary_in_window,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertIsNone(reason)
            mock_verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
