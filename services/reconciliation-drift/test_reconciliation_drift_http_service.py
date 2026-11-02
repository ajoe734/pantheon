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

    def test_scheduled_drift_report_approved_baseline_cites_drawdown_event_id_and_refuses_heartbeat_only(
        self,
    ) -> None:
        from services.execution.lean_runtime.paper_runtime import (
            RuntimeIdentity,
            RuntimeTelemetryEmitter,
        )
        from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore

        identity = RuntimeIdentity.from_env(
            {
                "PANTHEON_RUNTIME_ROLE": "paper",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_RUNTIME_ID": "rt-sched-dd-001",
                "PANTHEON_WORKSPACE_REF": "workspace-sched-dd",
                "PANTHEON_AUTH_PROFILE_REF": "auth-sched-dd",
                "PANTHEON_PERSONA_ID": "persona-sched-dd",
                "PANTHEON_SESSION_ID": "session-sched-dd",
                "PANTHEON_TRACE_ID": "trace-sched-dd-001",
                "PANTHEON_REQUEST_ID": "request-sched-dd-001",
            }
        )

        class _Resolver:
            def resolve(self):
                return {
                    "binding_id": "bind-sched-dd-001",
                    "runtime_id": "rt-sched-dd-001",
                    "plan_id": "plan-sched-dd-001",
                    "capital_pool_id": "pool-sched-dd-001",
                    "persona_capital_binding_id": "pcb-sched-dd-001",
                    "artifact_id": "artifact-tw-session-momentum-v1",
                    "artifact_version": "1.0.0",
                    "deployment_stage": "paper",
                    "deployment_mode": "paper",
                    "effective_at": "2026-10-10T00:00:00Z",
                }

        emitter = RuntimeTelemetryEmitter(identity, _Resolver())
        summary_store = RuntimeSummaryProjectionStore(path=None)
        hb_1 = emitter.build_event(
            "heartbeat",
            {"queue_lag_ms": 10.0, "delivery_lag_ms": 20.0},
            created_at="2026-10-10T09:58:00Z",
        )
        hb_only_summary = summary_store.project_event(hb_1)
        hb_only_summary["trace_id"] = hb_1["trace_id"]
        hb_only_ids = _summary_telemetry_event_ids(hb_only_summary)

        baseline_check = {
            "metric": "drawdown",
            "status": "warning",
            "observed": 0.052,
            "baseline": 0.04,
            "delta": 0.012,
            "relative_delta": 0.3,
            "observed_multiple": 1.3,
            "governed_threshold_breached": True,
            "threshold_metric": "rolling_drawdown_multiple",
            "comparator": "gt",
            "threshold_value": 1.25,
            "threshold_window": "paper-daily-sweep",
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md#7.1;services/evolution/config/threshold_sweep_thresholds.json",
            "signal_type": "performance_degradation",
            "baseline_source": "services/evolution/config/artifact_baselines.json#artifact-tw-session-momentum-v1@1.0.0",
        }
        eval_hb_only: Dict[str, Any] = {
            "evaluation_id": "eval-sched-dd-hb-only",
            "binding_id": "bind-sched-dd-001",
            "runtime_id": "rt-sched-dd-001",
            "tenant_id": "default",
            "reconciliation_checks": [],
            "drift_checks": [dict(baseline_check)],
        }
        # Heartbeat-only summary has no drawdown_event_id -> report must not be built
        self.assertIsNone(hb_only_summary.get("drawdown_event_id"))
        self.assertIsNone(
            _scheduled_drift_report(
                summary=hb_only_summary,
                evaluation=eval_hb_only,
                telemetry_event_ids=hb_only_ids,
                timestamp="2026-10-10T10:00:00Z",
            )
        )

        # Now project a real drawdown snapshot followed by a newer heartbeat
        dd_evt = emitter.build_event(
            "drawdown_snapshot",
            {"drawdown": 0.052, "drawdown_as_of": "2026-10-10T09:59:00Z"},
            created_at="2026-10-10T09:59:00Z",
        )
        hb_2 = emitter.build_event(
            "heartbeat",
            {"queue_lag_ms": 11.0, "delivery_lag_ms": 21.0},
            created_at="2026-10-10T09:59:30Z",
        )
        summary_store.project_event(dd_evt)
        summary_with_dd = summary_store.project_event(hb_2)
        summary_with_dd["trace_id"] = dd_evt["trace_id"]
        all_ids = _summary_telemetry_event_ids(summary_with_dd)
        self.assertIn(dd_evt["event_id"], all_ids)

        eval_dd: Dict[str, Any] = {
            "evaluation_id": "eval-sched-dd-causal",
            "binding_id": "bind-sched-dd-001",
            "runtime_id": "rt-sched-dd-001",
            "tenant_id": "default",
            "reconciliation_checks": [],
            "drift_checks": [dict(baseline_check)],
        }
        report = _scheduled_drift_report(
            summary=summary_with_dd,
            evaluation=eval_dd,
            telemetry_event_ids=all_ids,
            timestamp="2026-10-10T10:00:00Z",
        )
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report["telemetry_event_ids"], [dd_evt["event_id"]])
        self.assertNotIn(hb_1["event_id"], report["telemetry_event_ids"])
        self.assertNotIn(hb_2["event_id"], report["telemetry_event_ids"])
        self.assertEqual(report["severity"], "medium")
        self.assertEqual(report["threshold_metric"], "rolling_drawdown_multiple")
        self.assertAlmostEqual(report["observed"], 1.3)
        self.assertEqual(report["comparator"], "gt")
        self.assertAlmostEqual(report["threshold_value"], 1.25)
        self.assertEqual(report["threshold_window"], "paper-daily-sweep")

        incident = build_incident_from_drift_report(report)
        self.assertEqual(incident.telemetry_event_ids, [dd_evt["event_id"]])
        self.assertIn("threshold_metric=rolling_drawdown_multiple", incident.evidence_summary or "")
        self.assertIn("observed=1.3", incident.evidence_summary or "")
        self.assertIn("threshold_value=1.25", incident.evidence_summary or "")


class TestAcceptedAppendVisibilityReason(unittest.TestCase):
    def setUp(self) -> None:
        self.binding_id = "rb-test-001"
        self.runtime_id = "rt-test-001"
        self.artifact_id = "art-test-001"
        self.artifact_version = "1.0.0"
        self.tenant_id = "tenant-test"
        self.accepted_event_id = "evt-accepted-001"
        self.observed_event_id = "evt-observed-002"
        self.telemetry_url = "http://telemetry.local:8083"
        self.timestamp = "2026-10-11T12:00:00Z"

        self.accepted_evaluation = {
            "evaluation_id": "eval-accepted-001",
            "binding_id": self.binding_id,
            "runtime_id": self.runtime_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "tenant_id": self.tenant_id,
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
            "runtime_id": self.runtime_id,
            "artifact_id": self.artifact_id,
            "artifact_version": self.artifact_version,
            "tenant_id": self.tenant_id,
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
            mock_verify.assert_called_once_with(
                self.telemetry_url,
                accepted_event_id=self.accepted_event_id,
                observed_event_id=self.observed_event_id,
                tenant_id=self.tenant_id,
                service_token=None,
                timeout_seconds=5.0,
                expected_binding_id=self.binding_id,
                expected_runtime_id=self.runtime_id,
                expected_artifact_id=self.artifact_id,
                expected_artifact_version=self.artifact_version,
            )

    def test_evicted_missing_current_identity_fails_closed_without_calling_durable_order(self) -> None:
        for missing_key in ("runtime_id", "artifact_id", "artifact_version"):
            bad_summary = dict(self.summary_evicted)
            bad_summary[missing_key] = ""
            with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
                reason, _ = _accepted_append_visibility_reason(
                    summary=bad_summary,
                    binding_id=self.binding_id,
                    timestamp=self.timestamp,
                    evaluations=[self.accepted_evaluation],
                    telemetry_url=self.telemetry_url,
                )
                self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
                mock_verify.assert_not_called()

        mismatched_summary = dict(self.summary_evicted)
        mismatched_summary["binding_id"] = "different-binding"
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            reason, _ = _accepted_append_visibility_reason(
                summary=mismatched_summary,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
            mock_verify.assert_not_called()

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

    def test_evicted_confirmed_fails_closed_when_coercible_numeric_anchors(self) -> None:
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            # 1. Numeric tenant_id in evaluation
            bad_eval = dict(self.accepted_evaluation)
            bad_eval["tenant_id"] = 123
            reason, _ = _accepted_append_visibility_reason(
                summary=self.summary_evicted,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[bad_eval],
                telemetry_url=self.telemetry_url,
                tenant_id=123,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
            mock_verify.assert_not_called()

            # 2. Numeric runtime_id in summary
            bad_sum = dict(self.summary_evicted)
            bad_sum["runtime_id"] = 123
            reason, _ = _accepted_append_visibility_reason(
                summary=bad_sum,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
            mock_verify.assert_not_called()

            # 3. Falsy 0, False, present blank "" in summary tenant_id does not infer default tenant
            for falsy_tid in (0, False, "", "   "):
                bad_t_sum = dict(self.summary_evicted)
                bad_t_sum["tenant_id"] = falsy_tid
                reason, _ = _accepted_append_visibility_reason(
                    summary=bad_t_sum,
                    binding_id=self.binding_id,
                    timestamp=self.timestamp,
                    evaluations=[self.accepted_evaluation],
                    telemetry_url=self.telemetry_url,
                )
                self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
                mock_verify.assert_not_called()

            # 4. Conflicting declared runtime_binding_id in summary
            conflict_sum = dict(self.summary_evicted)
            conflict_sum["runtime_binding_id"] = "conflicting-runtime-binding"
            reason, _ = _accepted_append_visibility_reason(
                summary=conflict_sum,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
            mock_verify.assert_not_called()

    def test_missing_or_null_current_tenant_without_explicit_scope_fails_closed(self) -> None:
        for missing_tenant_summary in (
            {k: v for k, v in self.summary_evicted.items() if k != "tenant_id"},
            dict(self.summary_evicted, tenant_id=None),
        ):
            with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
                reason, _ = _accepted_append_visibility_reason(
                    summary=missing_tenant_summary,
                    binding_id=self.binding_id,
                    timestamp=self.timestamp,
                    evaluations=[self.accepted_evaluation],
                    telemetry_url=self.telemetry_url,
                    tenant_id=None,
                )
                self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
                mock_verify.assert_not_called()

    def test_missing_both_current_binding_aliases_fails_closed(self) -> None:
        no_binding_summary = {k: v for k, v in self.summary_evicted.items() if k not in ("binding_id", "runtime_binding_id")}
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            reason, _ = _accepted_append_visibility_reason(
                summary=no_binding_summary,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
            mock_verify.assert_not_called()

    def test_runtime_binding_id_alias_only_supported(self) -> None:
        alias_summary = {k: v for k, v in self.summary_evicted.items() if k != "binding_id"}
        alias_summary["runtime_binding_id"] = self.binding_id
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            mock_verify.return_value = (
                True,
                None,
                {
                    "accepted_event_id": self.accepted_event_id,
                    "observed_event_id": self.observed_event_id,
                    "binding_id": self.binding_id,
                },
            )
            reason, _ = _accepted_append_visibility_reason(
                summary=alias_summary,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertIsNone(reason)
            mock_verify.assert_called_once()

    def test_explicit_last_lifecycle_identity_fields_qualify_and_enforce_consistency(self) -> None:
        summary_no_top_tenant = {k: v for k, v in self.summary_evicted.items() if k != "tenant_id"}
        summary_no_top_tenant["last_lifecycle_identity"] = dict(
            summary_no_top_tenant["last_lifecycle_identity"],
            tenant_id=self.tenant_id,
        )
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            mock_verify.return_value = (
                True,
                None,
                {
                    "accepted_event_id": self.accepted_event_id,
                    "observed_event_id": self.observed_event_id,
                    "binding_id": self.binding_id,
                },
            )
            reason, _ = _accepted_append_visibility_reason(
                summary=summary_no_top_tenant,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertIsNone(reason)
            mock_verify.assert_called_once()

        conflicting_summary = dict(self.summary_evicted)
        conflicting_summary["last_lifecycle_identity"] = dict(
            conflicting_summary["last_lifecycle_identity"],
            tenant_id="conflicting-tenant",
        )
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            reason, _ = _accepted_append_visibility_reason(
                summary=conflicting_summary,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
            mock_verify.assert_not_called()

    def test_numeric_observed_event_id_not_coerced(self) -> None:
        bad_sum = dict(self.summary_evicted)
        bad_sum["last_lifecycle_identity"] = dict(bad_sum["last_lifecycle_identity"], event_id=123)
        with mock.patch("telemetry_client.verify_durable_event_order") as mock_verify:
            reason, _ = _accepted_append_visibility_reason(
                summary=bad_sum,
                binding_id=self.binding_id,
                timestamp=self.timestamp,
                evaluations=[self.accepted_evaluation],
                telemetry_url=self.telemetry_url,
            )
            self.assertEqual(reason, "accepted_lifecycle_append_not_visible")
            mock_verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
