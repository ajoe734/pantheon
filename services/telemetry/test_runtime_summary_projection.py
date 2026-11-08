"""Tests for telemetry-owned runtime status projection."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore


def _event(event_type: str = "heartbeat", *, created_at: str = "2026-05-01T00:00:00Z", stage: str = "paper"):
    return {
        "event_id": f"evt-{stage}-{event_type}",
        "event_type": event_type,
        "created_at": created_at,
        "deployment_stage": stage,
        "environment": stage,
        "binding_id": f"rtb-{stage}-001",
        "runtime_id": f"rt-{stage}-001",
        "capital_pool_id": f"pool-{stage}-001",
        "artifact_id": f"artifact-{stage}-001",
        "artifact_version": "1.0.0",
        "plan_id": f"plan-{stage}-001",
        "persona_capital_binding_id": f"pcb-{stage}-001",
        "target": {"strategy_id": f"strategy-{stage}-001"},
        "metrics": {"heartbeat": 1} if event_type == "heartbeat" else {"action": event_type},
        "metadata": {
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
            "runtime_adapter_version": "0.1.0",
        },
    }


def _runtime_heartbeat_event(stage: str = "paper"):
    event = _event(created_at="2026-05-01T00:00:05Z", stage=stage)
    event["metrics"].update({"queue_lag_ms": 3, "event_delivery_lag_ms": 8})
    event["metadata"].update(
        {
            "source_type": "runtime_heartbeat",
            "runtime_heartbeat": {
                "connectivity_status": "connected",
                "broker_status": "ok",
                "queue_lag_ms": 3,
                "event_delivery_lag_ms": 8,
                "health_summary": {"runtime": "ok"},
            },
            "connectivity_status": "connected",
            "broker_status": "ok",
        }
    )
    return event


def _lifecycle_event(
    event_id: str,
    *,
    created_at: str,
    sequence_no: int,
    aggregate_id: str = "tj-paper-001",
):
    event = _event("position_snapshot", created_at=created_at)
    event.update(
        {
            "event_id": event_id,
            "tenant_id": "tenant-001",
            "environment": "paper",
            "execution_mode": "paper",
            "trace_id": "trace-paper-001",
            "signal_id": "signal-paper-001",
            "run_id": "run-paper-001",
            "loop_run_id": "lr-run-paper-001",
            "aggregate_type": "trade_journey",
            "aggregate_id": aggregate_id,
            "sequence_no": sequence_no,
            "causal_parent_id": f"parent-{event_id}",
            "source_mode": "live",
            "authority_refs": {"persona_id": "persona-paper-001"},
            "correlation_envelope": {
                "schema_version": "trade-journey-envelope/1",
                "tenant_id": "tenant-001",
                "environment": "paper",
                "journey_id": aggregate_id,
                "correlation_id": "corr-paper-001",
                "trace_id": "trace-paper-001",
                "event_id": event_id,
                "causation_event_id": f"parent-{event_id}",
                "producer": "execution.paper_runtime",
                "event_time": created_at,
                "received_at": created_at,
                "producer_revision": 1,
            },
        }
    )
    event["metadata"].update(
        {
            "persona_id": "persona-paper-001",
            "signal_id": "signal-paper-001",
            "run_id": "run-paper-001",
            "sequence_no": sequence_no,
        }
    )
    return event


class RuntimeSummaryProjectionStoreTest(unittest.TestCase):
    def test_heartbeat_updates_runtime_summary_identity_and_bridge(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event())

        self.assertIsNotNone(summary)
        self.assertEqual(summary["runtime_id"], "rt-paper-001")
        self.assertEqual(summary["runtime_binding_id"], "rtb-paper-001")
        self.assertEqual(summary["deployment_stage"], "paper")
        self.assertEqual(summary["last_heartbeat_at"], "2026-05-01T00:00:00Z")
        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["engine_bridge_repo"], "ajoe734/pantheon-lean.git")
        self.assertEqual(summary["engine_bridge_commit"], "abc1234")
        self.assertEqual(summary["health_summary"]["telemetry"], "ok")
        self.assertEqual(summary["health_summary"]["paper_runtime"], "ok")

    def test_runtime_heartbeat_status_fields_are_projected(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_runtime_heartbeat_event())

        self.assertEqual(summary["last_heartbeat_at"], "2026-05-01T00:00:05Z")
        self.assertEqual(summary["connectivity_status"], "connected")
        self.assertEqual(summary["broker_status"], "ok")
        self.assertEqual(summary["queue_lag_ms"], 3)
        self.assertEqual(summary["event_delivery_lag_ms"], 8)
        self.assertEqual(summary["reported_health_summary"], {"runtime": "ok"})
        self.assertEqual(summary["health_summary"]["broker"], "ok")

    def test_trace_and_correlation_envelope_are_safely_projected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runtime_summaries.json"
            store = RuntimeSummaryProjectionStore(path, heartbeat_stale_after_seconds=60)
            event = _runtime_heartbeat_event()
            event["trace_id"] = "trace-paper-001"
            event["correlation_envelope"] = {
                "schema_version": "trade-journey-envelope/1",
                "tenant_id": "tenant-001",
                "environment": "paper",
                "journey_id": "tj-paper-001",
                "correlation_id": "corr-paper-001",
                "trace_id": "trace-paper-001",
                "event_id": "corr-event-paper-001",
                "causation_event_id": "signal-paper-001",
                "producer": "execution.paper_runtime",
                "event_time": "2026-05-01T00:00:05Z",
                "received_at": "2026-05-01T00:00:05Z",
                "producer_revision": 1,
            }

            summary = store.project_event(event)
            event["correlation_envelope"]["trace_id"] = "mutated-after-projection"
            reloaded = RuntimeSummaryProjectionStore(path, heartbeat_stale_after_seconds=60)

            self.assertEqual(summary["trace_id"], "trace-paper-001")
            self.assertEqual(
                reloaded.get("rt-paper-001")["correlation_envelope"]["trace_id"],
                "trace-paper-001",
            )

    def test_last_lifecycle_identity_survives_later_heartbeat(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runtime_summaries.json"
            store = RuntimeSummaryProjectionStore(
                path,
                heartbeat_stale_after_seconds=60,
            )
            lifecycle = _lifecycle_event(
                "position-paper-001",
                created_at="2026-05-01T00:00:04Z",
                sequence_no=5,
            )

            store.project_event(lifecycle)
            heartbeat = _runtime_heartbeat_event()
            heartbeat["tenant_id"] = "tenant-001"
            summary = store.project_event(heartbeat)
            reloaded = RuntimeSummaryProjectionStore(
                path,
                heartbeat_stale_after_seconds=60,
            ).get("rt-paper-001")

            identity = summary["last_lifecycle_identity"]
            self.assertEqual(identity["event_id"], lifecycle["event_id"])
            self.assertEqual(identity["sequence_no"], 5)
            self.assertEqual(
                identity["correlation_envelope"]["journey_id"],
                "tj-paper-001",
            )
            self.assertEqual(
                summary["recent_lifecycle_event_ids"],
                [lifecycle["event_id"]],
            )
            self.assertEqual(summary["last_event_type"], "heartbeat")
            self.assertEqual(summary["trace_id"], "trace-paper-001")
            self.assertEqual(summary["tenant_id"], "tenant-001")
            self.assertEqual(summary["aggregate_id"], "tj-paper-001")
            self.assertEqual(summary["sequence_no"], 5)
            self.assertEqual(
                summary["correlation_envelope"]["journey_id"],
                "tj-paper-001",
            )
            self.assertEqual(reloaded["trace_id"], "trace-paper-001")
            self.assertEqual(reloaded["aggregate_id"], "tj-paper-001")

    def test_same_runtime_id_is_isolated_by_tenant_across_restart_list_and_get(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runtime_summaries.json"
            store = RuntimeSummaryProjectionStore(
                path,
                heartbeat_stale_after_seconds=60,
            )
            lifecycle = _lifecycle_event(
                "position-tenant-alpha-001",
                created_at="2026-05-01T00:00:04Z",
                sequence_no=5,
            )
            beta_heartbeat = _runtime_heartbeat_event()
            beta_heartbeat.update(
                {
                    "event_id": "heartbeat-tenant-beta-001",
                    "tenant_id": "tenant-beta",
                }
            )

            store.project_event(lifecycle)
            beta_summary = store.project_event(beta_heartbeat)

            alpha_summary = store.get(
                "rt-paper-001",
                tenant_id="tenant-001",
            )
            self.assertEqual(alpha_summary["last_event_id"], lifecycle["event_id"])
            self.assertEqual(alpha_summary["trace_id"], "trace-paper-001")
            self.assertEqual(
                alpha_summary["last_lifecycle_identity"]["event_id"],
                lifecycle["event_id"],
            )
            self.assertEqual(beta_summary["tenant_id"], "tenant-beta")
            self.assertEqual(
                beta_summary["last_heartbeat_event_id"],
                beta_heartbeat["event_id"],
            )
            self.assertNotIn("last_lifecycle_identity", beta_summary)
            self.assertIsNone(store.get("rt-paper-001"))
            self.assertEqual(
                [item["tenant_id"] for item in store.list(tenant_id="tenant-001")],
                ["tenant-001"],
            )
            self.assertEqual(
                [item["tenant_id"] for item in store.list(tenant_id="tenant-beta")],
                ["tenant-beta"],
            )

            reloaded = RuntimeSummaryProjectionStore(
                path,
                heartbeat_stale_after_seconds=60,
            )
            reloaded_alpha = reloaded.get(
                "rt-paper-001",
                tenant_id="tenant-001",
            )
            reloaded_beta = reloaded.get(
                "rt-paper-001",
                tenant_id="tenant-beta",
            )
            self.assertEqual(reloaded_alpha["last_event_id"], lifecycle["event_id"])
            self.assertEqual(reloaded_alpha["tenant_id"], "tenant-001")
            self.assertEqual(
                reloaded_beta["last_heartbeat_event_id"],
                beta_heartbeat["event_id"],
            )
            self.assertEqual(reloaded_beta["tenant_id"], "tenant-beta")
            self.assertIsNone(reloaded.get("rt-paper-001"))
            self.assertEqual(len(reloaded.list()), 2)

    def test_six_runtime_summaries_keep_consumer_identity_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runtime_summaries.json"
            store = RuntimeSummaryProjectionStore(
                path,
                heartbeat_stale_after_seconds=60,
            )

            expected: dict[str, tuple[str, str]] = {}
            for index in range(1, 7):
                runtime_id = f"rt-paper-{index:03d}"
                trace_id = f"trace-paper-{index:03d}"
                journey_id = f"tj-paper-{index:03d}"
                binding_id = f"rtb-paper-{index:03d}"
                lifecycle = _lifecycle_event(
                    f"position-paper-{index:03d}",
                    created_at=f"2026-05-01T00:00:{index:02d}Z",
                    sequence_no=index,
                    aggregate_id=journey_id,
                )
                lifecycle.update(
                    {
                        "runtime_id": runtime_id,
                        "binding_id": binding_id,
                        "trace_id": trace_id,
                        "aggregate_id": journey_id,
                    }
                )
                lifecycle["correlation_envelope"].update(
                    {
                        "journey_id": journey_id,
                        "trace_id": trace_id,
                    }
                )
                store.project_event(lifecycle)

                heartbeat = _runtime_heartbeat_event()
                heartbeat.update(
                    {
                        "event_id": f"heartbeat-paper-{index:03d}",
                        "runtime_id": runtime_id,
                        "binding_id": binding_id,
                        "tenant_id": "tenant-001",
                        "created_at": f"2026-05-01T00:01:{index:02d}Z",
                    }
                )
                store.project_event(heartbeat)
                expected[runtime_id] = (trace_id, journey_id)

            summaries = RuntimeSummaryProjectionStore(
                path,
                heartbeat_stale_after_seconds=60,
            ).list()

            self.assertEqual(len(summaries), 6)
            for summary in summaries:
                trace_id, journey_id = expected[summary["runtime_id"]]
                self.assertEqual(summary["last_event_type"], "heartbeat")
                self.assertEqual(summary["tenant_id"], "tenant-001")
                self.assertEqual(summary["trace_id"], trace_id)
                self.assertEqual(summary["aggregate_type"], "trade_journey")
                self.assertEqual(summary["aggregate_id"], journey_id)
                self.assertEqual(
                    summary["correlation_envelope"]["journey_id"],
                    journey_id,
                )
                self.assertEqual(
                    summary["last_lifecycle_identity"]["trace_id"],
                    trace_id,
                )

    def test_recent_lifecycle_event_ids_are_ordered_deduplicated_and_bounded(self):
        store = RuntimeSummaryProjectionStore(
            heartbeat_stale_after_seconds=60,
            recent_lifecycle_event_limit=3,
        )
        for index in range(5):
            store.project_event(
                _lifecycle_event(
                    f"evt-lifecycle-{index}",
                    created_at=f"2026-05-01T00:00:0{index}Z",
                    sequence_no=index + 1,
                )
            )

        summary = store.project_event(
            _lifecycle_event(
                "evt-lifecycle-3",
                created_at="2026-05-01T00:00:06Z",
                sequence_no=4,
            )
        )

        self.assertEqual(
            summary["recent_lifecycle_event_ids"],
            ["evt-lifecycle-2", "evt-lifecycle-4", "evt-lifecycle-3"],
        )
        self.assertEqual(store.stats()["recent_lifecycle_event_limit"], 3)

    def test_binding_rollover_clears_recent_lifecycle_event_ids(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        old = _lifecycle_event(
            "evt-old-binding-lifecycle",
            created_at="2026-05-01T00:00:01Z",
            sequence_no=1,
        )
        old["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:00:00Z"
        store.project_event(old)

        new = _event("heartbeat", created_at="2026-05-01T00:10:01Z")
        new.update(
            {
                "event_id": "evt-new-binding-heartbeat",
                "binding_id": "rtb-paper-002",
                "artifact_id": "artifact-paper-002",
                "artifact_version": "2.0.0",
                "plan_id": "plan-paper-002",
            }
        )
        new["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:10:00Z"
        summary = store.project_event(new)

        self.assertEqual(summary["binding_id"], "rtb-paper-002")
        self.assertNotIn("recent_lifecycle_event_ids", summary)
        self.assertNotIn("last_lifecycle_identity", summary)

    def test_deploy_completed_sets_runtime_active_without_fabricating_heartbeat(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event("deploy_completed"))

        self.assertEqual(summary["state"], "active")
        self.assertNotIn("last_heartbeat_at", summary)
        self.assertEqual(summary["health_summary"]["telemetry"], "degraded")

    def test_stale_heartbeat_returns_degraded_summary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        store.project_event(_event(created_at="2026-05-01T00:00:00Z", stage="paper"))

        summary = store.get(
            "rt-paper-001",
            now=datetime(2026, 5, 1, 0, 2, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["state"], "degraded")
        self.assertEqual(summary["health_summary"]["telemetry"], "degraded")
        self.assertEqual(summary["staleness"]["threshold_seconds"], 60)

    def test_projection_persists_as_bff_readable_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runtime_summaries.json"
            store = RuntimeSummaryProjectionStore(path, heartbeat_stale_after_seconds=60)
            store.project_event(_event())

            reloaded = RuntimeSummaryProjectionStore(path, heartbeat_stale_after_seconds=60)

            self.assertEqual(
                reloaded.get("rt-paper-001")["runtime_binding_id"],
                "rtb-paper-001",
            )

    def test_canary_event_projects_canary_runtime_summary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event(stage="canary"))

        self.assertIsNotNone(summary)
        self.assertEqual(summary["runtime_id"], "rt-canary-001")
        self.assertEqual(summary["runtime_binding_id"], "rtb-canary-001")
        self.assertEqual(summary["deployment_stage"], "canary")
        self.assertEqual(summary["state"], "active")
        self.assertEqual(summary["health_summary"]["canary_runtime"], "ok")
        self.assertEqual(summary["health_summary"]["telemetry"], "ok")
        self.assertNotIn("paper_runtime", summary["health_summary"])

    def test_live_event_projects_live_runtime_summary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event(stage="live"))

        self.assertIsNotNone(summary)
        self.assertEqual(summary["runtime_id"], "rt-live-001")
        self.assertEqual(summary["deployment_stage"], "live")
        self.assertEqual(summary["health_summary"]["live_runtime"], "ok")
        self.assertNotIn("canary_runtime", summary["health_summary"])
        self.assertNotIn("paper_runtime", summary["health_summary"])

    def test_frozen_event_projects_frozen_runtime_summary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        summary = store.project_event(_event(stage="frozen"))

        self.assertIsNotNone(summary)
        self.assertEqual(summary["deployment_stage"], "frozen")
        self.assertEqual(summary["health_summary"]["frozen_runtime"], "ok")

    def test_unknown_stage_event_is_rejected(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        event = _event()
        event["deployment_stage"] = "simulation"
        result = store.project_event(event)

        self.assertIsNone(result)

    def test_performance_metrics_prefer_independent_explicit_as_of_timestamps(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        event = _event("pnl_snapshot", created_at="2026-05-01T00:10:00Z")
        event["metrics"] = {"pnl": 125.5, "drawdown_pct": 0.08}
        event["pnl_as_of"] = "2026-05-01T00:08:00Z"
        event["drawdown_as_of"] = "2026-05-01T00:09:00+00:00"

        summary = store.project_event(event)

        self.assertEqual(summary["pnl"], 125.5)
        self.assertEqual(summary["pnl_at"], "2026-05-01T00:08:00Z")
        self.assertEqual(summary["drawdown"], 0.08)
        self.assertEqual(summary["drawdown_at"], "2026-05-01T00:09:00+00:00")

    def test_drawdown_snapshot_sharpe_ratio_projects_with_provenance(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        event = _event("drawdown_snapshot", created_at="2026-05-01T00:10:00Z")
        event["metrics"] = {"drawdown_pct": 0.05, "sharpe_ratio": 1.75}

        summary = store.project_event(event)

        self.assertEqual(summary["sharpe_ratio"], 1.75)
        self.assertEqual(summary["sharpe_ratio_at"], "2026-05-01T00:10:00Z")
        self.assertEqual(summary["sharpe_ratio_binding_id"], "rtb-paper-001")

    def test_performance_metrics_fall_back_to_created_at_for_legacy_events(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        event = _event("drawdown_snapshot", created_at="2026-05-01T00:10:00Z")
        event["metrics"] = {"pnl": -25.0, "drawdown_pct": 0.12}

        summary = store.project_event(event)

        self.assertEqual(summary["pnl_at"], "2026-05-01T00:10:00Z")
        self.assertEqual(summary["drawdown_at"], "2026-05-01T00:10:00Z")

    def test_invalid_explicit_metric_as_of_falls_back_to_created_at(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        event = _event("pnl_snapshot", created_at="2026-05-01T00:10:00Z")
        event["metrics"] = {"pnl": 5.0, "drawdown_pct": 0.02}
        event["pnl_as_of"] = "not-a-timestamp"
        event["drawdown_as_of"] = "2026-05-01T00:09:00"  # no timezone

        summary = store.project_event(event)

        self.assertEqual(summary["pnl_at"], "2026-05-01T00:10:00Z")
        self.assertEqual(summary["drawdown_at"], "2026-05-01T00:10:00Z")

    def test_threshold_derived_echo_does_not_refresh_explicit_metric_as_of(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        genuine = _event("drawdown_snapshot", created_at="2026-05-01T00:05:00Z")
        genuine["metrics"] = {"pnl": -10.0, "drawdown_pct": 0.10}
        genuine["pnl_as_of"] = "2026-05-01T00:03:00Z"
        genuine["drawdown_as_of"] = "2026-05-01T00:04:00Z"
        store.project_event(genuine)

        derived = _event("drawdown_snapshot", created_at="2026-05-01T00:20:00Z")
        derived["event_id"] = "evt-derived-threshold-echo"
        derived["metrics"] = {"pnl": -999.0, "drawdown_pct": 0.99}
        derived["pnl_as_of"] = "2026-05-01T00:18:00Z"
        derived["drawdown_as_of"] = "2026-05-01T00:19:00Z"
        derived["metadata"]["derived_from_threshold_evaluation"] = True

        summary = store.project_event(derived)

        self.assertEqual(summary["pnl"], -10.0)
        self.assertEqual(summary["pnl_at"], "2026-05-01T00:03:00Z")
        self.assertEqual(summary["drawdown"], 0.10)
        self.assertEqual(summary["drawdown_at"], "2026-05-01T00:04:00Z")

    def test_older_metric_observations_do_not_regress_independent_values(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        initial = _event("pnl_snapshot", created_at="2026-05-01T00:20:00Z")
        initial["metrics"] = {"pnl": 100.0, "drawdown_pct": 0.10}
        initial["pnl_as_of"] = "2026-05-01T00:10:00Z"
        initial["drawdown_as_of"] = "2026-05-01T00:10:00Z"
        store.project_event(initial)

        older = _event("drawdown_snapshot", created_at="2026-05-01T00:30:00Z")
        older["event_id"] = "evt-unique-older-observations"
        older["metrics"] = {"pnl": -50.0, "drawdown_pct": 0.40}
        older["pnl_as_of"] = "2026-05-01T00:09:00Z"
        older["drawdown_as_of"] = "2026-05-01T00:09:00Z"
        summary = store.project_event(older)

        self.assertEqual(summary["pnl"], 100.0)
        self.assertEqual(summary["pnl_at"], "2026-05-01T00:10:00Z")
        self.assertEqual(summary["drawdown"], 0.10)
        self.assertEqual(summary["drawdown_at"], "2026-05-01T00:10:00Z")

        mixed = _event("pnl_snapshot", created_at="2026-05-01T00:40:00Z")
        mixed["event_id"] = "evt-independent-metric-observations"
        mixed["metrics"] = {"pnl": 125.0, "drawdown_pct": 0.50}
        mixed["pnl_as_of"] = "2026-05-01T00:11:00Z"
        mixed["drawdown_as_of"] = "2026-05-01T00:08:00Z"
        summary = store.project_event(mixed)

        self.assertEqual(summary["pnl"], 125.0)
        self.assertEqual(summary["pnl_at"], "2026-05-01T00:11:00Z")
        self.assertEqual(summary["drawdown"], 0.10)
        self.assertEqual(summary["drawdown_at"], "2026-05-01T00:10:00Z")

    def test_later_created_at_alone_cannot_roll_over_a_legacy_binding(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        old = _event("pnl_snapshot", created_at="2026-05-01T00:05:00Z")
        old["metrics"] = {"pnl": 50.0, "drawdown_pct": 0.02}
        store.project_event(old)

        new = _event("heartbeat", created_at="2026-05-01T00:10:00Z")
        new.update(
            {
                "event_id": "evt-binding-002-heartbeat",
                "binding_id": "rtb-paper-002",
                "artifact_id": "artifact-paper-002",
                "artifact_version": "2.0.0",
                "plan_id": "plan-paper-002",
            }
        )
        summary = store.project_event(new)

        self.assertEqual(summary["binding_id"], "rtb-paper-001")
        self.assertEqual(summary["artifact_id"], "artifact-paper-001")
        self.assertEqual(summary["last_event_id"], old["event_id"])
        self.assertEqual(summary["pnl"], 50.0)
        self.assertEqual(summary["drawdown"], 0.02)
        self.assertEqual(
            summary["projection_diagnostics"]["last_binding_rollover_rejection"][
                "reason"
            ],
            "binding_effective_boundary_unavailable",
        )

    def test_retired_binding_late_event_cannot_reclaim_after_rollover(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "runtime_summaries.json"
            store = RuntimeSummaryProjectionStore(path, heartbeat_stale_after_seconds=60)
            old = _event(created_at="2026-05-01T00:05:00Z")
            old["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:00:00Z"
            store.project_event(old)

            new = _event("heartbeat", created_at="2026-05-01T00:10:00Z")
            new.update(
                {
                    "event_id": "evt-binding-002-heartbeat",
                    "binding_id": "rtb-paper-002",
                    "artifact_id": "artifact-paper-002",
                    "artifact_version": "2.0.0",
                    "plan_id": "plan-paper-002",
                }
            )
            new["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:10:00Z"
            store.project_event(new)

            # Reload to prove that the generation boundary and retired-binding
            # tombstone survive the JSON read-model restart.
            reloaded = RuntimeSummaryProjectionStore(path, heartbeat_stale_after_seconds=60)
            late_old = _event("pnl_snapshot", created_at="2026-05-01T00:20:00Z")
            late_old["event_id"] = "evt-late-retired-binding-001"
            late_old["metrics"] = {"pnl": 999.0, "drawdown_pct": 0.99}
            summary = reloaded.project_event(late_old)

            self.assertEqual(summary["binding_id"], "rtb-paper-002")
            self.assertEqual(summary["artifact_id"], "artifact-paper-002")
            self.assertEqual(summary["last_event_id"], "evt-binding-002-heartbeat")
            self.assertNotIn("pnl", summary)
            diagnostic = summary["projection_diagnostics"][
                "last_binding_rollover_rejection"
            ]
            self.assertEqual(diagnostic["reason"], "retired_binding_reclaim")
            self.assertEqual(diagnostic["candidate_binding_id"], "rtb-paper-001")
            self.assertEqual(diagnostic["current_binding_id"], "rtb-paper-002")

    def test_projection_first_binding_rejects_unseen_late_binding(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        current = _event("heartbeat", created_at="2026-05-01T00:11:00Z")
        current.update(
            {
                "event_id": "evt-binding-002-first-projected",
                "binding_id": "rtb-paper-002",
                "artifact_id": "artifact-paper-002",
                "artifact_version": "2.0.0",
                "plan_id": "plan-paper-002",
            }
        )
        current["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:10:00Z"
        store.project_event(current)

        # B1 was never observed by this projection, so it is not a tombstone.
        # Its later event time still cannot stand in for binding generation.
        unseen_old = _event("pnl_snapshot", created_at="2026-05-01T00:20:00Z")
        unseen_old["event_id"] = "evt-unseen-binding-001-late"
        unseen_old["metrics"] = {"pnl": 999.0, "drawdown_pct": 0.99}
        summary = store.project_event(unseen_old)

        self.assertEqual(summary["binding_id"], "rtb-paper-002")
        self.assertEqual(summary["last_event_id"], "evt-binding-002-first-projected")
        self.assertNotIn("pnl", summary)
        self.assertEqual(
            summary["projection_diagnostics"]["last_binding_rollover_rejection"][
                "reason"
            ],
            "candidate_binding_effective_at_missing",
        )

    def test_binding_effective_metadata_allows_true_newer_rollover(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        old = _event(created_at="2026-05-01T00:05:00Z")
        old["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:00:00Z"
        store.project_event(old)

        new = _event("heartbeat", created_at="2026-05-01T00:11:00Z")
        new.update(
            {
                "event_id": "evt-effective-binding-002",
                "binding_id": "rtb-paper-002",
                "artifact_id": "artifact-paper-002",
                "artifact_version": "2.0.0",
                "plan_id": "plan-paper-002",
            }
        )
        new["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:10:00Z"

        summary = store.project_event(new)

        self.assertEqual(summary["binding_id"], "rtb-paper-002")
        self.assertEqual(summary["_binding_effective_at"], "2026-05-01T00:10:00Z")
        self.assertEqual(
            summary["_binding_boundary_source"],
            "metadata.runtime_binding_effective_at",
        )

    def test_candidate_effective_at_can_upgrade_legacy_binding_boundary(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        store.project_event(_event(created_at="2026-05-01T00:05:00Z"))

        upgraded = _event("heartbeat", created_at="2026-05-01T00:11:00Z")
        upgraded.update(
            {
                "event_id": "evt-effective-upgrade-binding-002",
                "binding_id": "rtb-paper-002",
                "artifact_id": "artifact-paper-002",
                "artifact_version": "2.0.0",
                "plan_id": "plan-paper-002",
            }
        )
        upgraded["metadata"]["binding_effective_at"] = "2026-05-01T00:10:00Z"
        summary = store.project_event(upgraded)

        self.assertEqual(summary["binding_id"], "rtb-paper-002")
        self.assertEqual(summary["artifact_id"], "artifact-paper-002")
        self.assertEqual(summary["_binding_effective_at"], "2026-05-01T00:10:00Z")
        self.assertEqual(summary["_retired_binding_ids"], ["rtb-paper-001"])

    def test_multiple_stages_coexist_without_collision(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        fresh = datetime(2026, 5, 1, 0, 0, 30, tzinfo=timezone.utc)

        store.project_event(_event(stage="paper"))
        store.project_event(_event(stage="canary"))
        store.project_event(_event(stage="live"))

        summaries = store.list(now=fresh)
        self.assertEqual(len(summaries), 3)
        stages = {s["deployment_stage"] for s in summaries}
        self.assertEqual(stages, {"paper", "canary", "live"})

        paper = store.get("rt-paper-001", now=fresh)
        self.assertEqual(paper["health_summary"]["paper_runtime"], "ok")

        canary = store.get("rt-canary-001", now=fresh)
        self.assertEqual(canary["health_summary"]["canary_runtime"], "ok")

        live = store.get("rt-live-001", now=fresh)
        self.assertEqual(live["health_summary"]["live_runtime"], "ok")

    def test_canary_stale_heartbeat_degrades_canary_runtime_key(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        store.project_event(_event(created_at="2026-05-01T00:00:00Z", stage="canary"))

        summary = store.get(
            "rt-canary-001",
            now=datetime(2026, 5, 1, 0, 2, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(summary["state"], "degraded")
        self.assertEqual(summary["health_summary"]["telemetry"], "degraded")
        self.assertEqual(summary["health_summary"]["canary_runtime"], "degraded")

    def test_native_direct_paper_heartbeat_derives_bridge_not_applicable(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        hb = _event(created_at="2026-05-01T00:00:05Z", stage="paper")
        hb["metadata"] = {
            "runtime_package": "paper_execution_runtime",
            "runtime_heartbeat": {
                "connectivity_status": "connected",
                "broker_status": "not_applicable",
                "queue_lag_ms": 1873,
                "event_delivery_lag_ms": 1112,
            },
            "connectivity_status": "connected",
            "broker_status": "not_applicable",
        }
        summary = store.project_event(hb)

        self.assertIsNotNone(summary)
        self.assertEqual(summary["runtime_package"], "paper_execution_runtime")
        self.assertEqual(summary["health_summary"]["bridge"], "not_applicable")
        self.assertEqual(summary["health_summary"]["paper_runtime"], "ok")
        self.assertEqual(summary["health_summary"]["telemetry"], "ok")
        self.assertEqual(summary["health_summary"]["broker"], "not_applicable")
        self.assertNotIn("engine_bridge_repo", summary)
        self.assertNotIn("engine_bridge_commit", summary)

    def test_unknown_and_bridge_requiring_profiles_missing_identity_remain_degraded(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        hb_unknown = _event(created_at="2026-05-01T00:00:05Z", stage="paper")
        hb_unknown["metadata"] = {"runtime_package": "execution_sidecar"}
        summary_unknown = store.project_event(hb_unknown)
        self.assertEqual(summary_unknown["health_summary"]["bridge"], "degraded")

        store_no_profile = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        hb_no_profile = _event(created_at="2026-05-01T00:00:06Z", stage="paper")
        hb_no_profile["event_id"] = "evt-paper-no-profile"
        hb_no_profile["metadata"] = {}
        summary_no_profile = store_no_profile.project_event(hb_no_profile)
        self.assertEqual(summary_no_profile["health_summary"]["bridge"], "degraded")

        store_canary = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        hb_canary = _event(created_at="2026-05-01T00:00:07Z", stage="canary")
        hb_canary["metadata"] = {"runtime_package": "paper_execution_runtime"}
        summary_canary = store_canary.project_event(hb_canary)
        self.assertEqual(summary_canary["health_summary"]["bridge"], "degraded")

        store_unknown_env = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        hb_unknown_env = _event(created_at="2026-05-01T00:00:08Z", stage="paper")
        hb_unknown_env["event_id"] = "evt-paper-unknown-env"
        hb_unknown_env["environment"] = "unknown"
        hb_unknown_env["metadata"] = {"runtime_package": "paper_execution_runtime"}
        summary_unknown_env = store_unknown_env.project_event(hb_unknown_env)
        self.assertEqual(summary_unknown_env["health_summary"]["bridge"], "degraded")

        store_missing_env = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        hb_missing_env = _event(created_at="2026-05-01T00:00:09Z", stage="paper")
        hb_missing_env["event_id"] = "evt-paper-missing-env"
        hb_missing_env["environment"] = ""
        hb_missing_env["metadata"] = {"runtime_package": "paper_execution_runtime"}
        summary_missing_env = store_missing_env.project_event(hb_missing_env)
        self.assertEqual(summary_missing_env["health_summary"]["bridge"], "degraded")

        store_top_level = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        hb_top_level = _event(created_at="2026-05-01T00:00:10Z", stage="paper")
        hb_top_level["event_id"] = "evt-paper-top-level-alias"
        hb_top_level["runtime_package"] = "paper_execution_runtime"
        hb_top_level["metadata"] = {}
        summary_top_level = store_top_level.project_event(hb_top_level)
        self.assertNotIn("runtime_package", summary_top_level)
        self.assertEqual(summary_top_level["health_summary"]["bridge"], "degraded")

        store_self_reported = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        hb_self_reported = _event(created_at="2026-05-01T00:00:11Z", stage="paper")
        hb_self_reported["event_id"] = "evt-paper-self-reported"
        hb_self_reported["metadata"] = {
            "reported_health_summary": {"bridge": "ok"},
            "runtime_heartbeat": {"health_summary": {"bridge": "ok"}},
        }
        summary_self_reported = store_self_reported.project_event(hb_self_reported)
        self.assertEqual(summary_self_reported["health_summary"]["bridge"], "degraded")

    def test_binding_rollover_clears_profile_and_prevents_inheritance(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        initial = _event(created_at="2026-05-01T00:00:05Z", stage="paper")
        initial["metadata"] = {
            "runtime_package": "paper_execution_runtime",
            "runtime_binding_effective_at": "2026-05-01T00:00:00Z",
        }
        summary = store.project_event(initial)
        self.assertEqual(summary["health_summary"]["bridge"], "not_applicable")
        self.assertEqual(summary["runtime_package"], "paper_execution_runtime")

        newer = _event("heartbeat", created_at="2026-05-01T00:10:00Z", stage="paper")
        newer.update(
            {
                "event_id": "evt-rollover-new-heartbeat",
                "binding_id": "rtb-paper-002",
                "artifact_id": "artifact-paper-002",
            }
        )
        newer["metadata"] = {
            "runtime_binding_effective_at": "2026-05-01T00:10:00Z",
        }
        summary_new = store.project_event(newer)
        self.assertEqual(summary_new["binding_id"], "rtb-paper-002")
        self.assertNotIn("runtime_package", summary_new)
        self.assertEqual(summary_new["health_summary"]["bridge"], "degraded")

        late_old = _event("heartbeat", created_at="2026-05-01T00:20:00Z", stage="paper")
        late_old["event_id"] = "evt-late-old-reclaim"
        late_old["binding_id"] = "rtb-paper-001"
        late_old["metadata"] = {"runtime_package": "paper_execution_runtime"}
        summary_reclaim = store.project_event(late_old)
        self.assertEqual(summary_reclaim["binding_id"], "rtb-paper-002")
        self.assertNotIn("runtime_package", summary_reclaim)
        self.assertEqual(summary_reclaim["health_summary"]["bridge"], "degraded")

    def test_failed_or_stale_native_runtime_remains_degraded(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        hb = _event(created_at="2026-05-01T00:00:00Z", stage="paper")
        hb["metadata"] = {
            "runtime_package": "paper_execution_runtime",
            "runtime_heartbeat": {"connectivity_status": "disconnected"},
            "connectivity_status": "disconnected",
        }
        summary = store.project_event(hb)
        self.assertEqual(summary["health_summary"]["paper_runtime"], "degraded")
        self.assertEqual(summary["health_summary"]["telemetry"], "degraded")

        fresh_hb = _event(created_at="2026-05-01T00:00:00Z", stage="paper")
        fresh_hb["event_id"] = "evt-stale-check"
        fresh_hb["metadata"] = {
            "runtime_package": "paper_execution_runtime",
            "runtime_heartbeat": {"connectivity_status": "connected"},
            "connectivity_status": "connected",
        }
        store.project_event(fresh_hb)
        stale_summary = store.get(
            "rt-paper-001",
            now=datetime(2026, 5, 1, 0, 2, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(stale_summary["state"], "degraded")
        self.assertEqual(stale_summary["health_summary"]["paper_runtime"], "degraded")
        self.assertEqual(stale_summary["health_summary"]["telemetry"], "degraded")
        self.assertEqual(stale_summary["health_summary"]["bridge"], "not_applicable")

    def _retired_event(self, created_at="2026-05-01T00:30:00Z"):
        event = _event("runtime_retired", created_at=created_at)
        event["event_id"] = "evt-paper-retired"
        event["metadata"]["retired_at"] = created_at
        return event

    def test_retirement_marks_summary_retired_and_excludes_by_default(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        store.project_event(_event())
        summary = store.project_event(self._retired_event())

        self.assertTrue(summary["retired"])
        self.assertEqual(summary["state"], "retired")
        self.assertEqual(summary["retired_at"], "2026-05-01T00:30:00Z")
        self.assertEqual(store.list(), [])
        listed = store.list(include_retired=True)
        self.assertEqual([item["runtime_id"] for item in listed], ["rt-paper-001"])
        self.assertEqual(listed[0]["state"], "retired")

    def test_failed_terminal_retirement_event_excludes_by_default(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        store.project_event(_event())
        event = self._retired_event()
        event["metadata"]["terminal_status"] = "failed"
        summary = store.project_event(event)

        self.assertTrue(summary["retired"])
        self.assertEqual(store.list(), [])
        self.assertEqual(
            [item["runtime_id"] for item in store.list(include_retired=True)],
            ["rt-paper-001"],
        )

    def test_retirement_stays_retired_whichever_side_of_last_heartbeat(self):
        for order in ("before", "after"):
            with self.subTest(order=order):
                store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
                beat = _event(created_at="2026-05-01T00:40:00Z")
                if order == "before":
                    store.project_event(self._retired_event())
                    store.project_event(beat)
                else:
                    store.project_event(beat)
                    store.project_event(self._retired_event("2026-05-01T00:50:00Z"))
                self.assertEqual(store.list(), [])
                summary = store.get("rt-paper-001")
                self.assertTrue(summary["retired"])
                self.assertEqual(summary["state"], "retired")

    def test_retirement_replay_is_idempotent(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        first = store.project_event(self._retired_event())
        second = store.project_event(self._retired_event())
        self.assertEqual(first["retired_at"], second["retired_at"])
        self.assertEqual(len(store.list(include_retired=True)), 1)

    def test_newer_binding_on_same_runtime_is_not_retired(self):
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
        old = _event(created_at="2026-05-01T00:05:00Z")
        old["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:00:00Z"
        store.project_event(old)
        store.project_event(self._retired_event("2026-05-01T00:10:00Z"))

        new = _event("heartbeat", created_at="2026-05-01T00:11:00Z")
        new.update({"event_id": "evt-new", "binding_id": "rtb-paper-002"})
        new["metadata"]["runtime_binding_effective_at"] = "2026-05-01T00:10:00Z"
        summary = store.project_event(new)

        self.assertEqual(summary["binding_id"], "rtb-paper-002")
        self.assertFalse(summary.get("retired", False))
        self.assertEqual(len(store.list()), 1)


if __name__ == "__main__":
    unittest.main()


def _fill_event(*, symbol="AAPL.US", qty=7.0, price=100.0, created_at="2026-05-01T00:01:00Z", stage="paper"):
    ev = _event(event_type="paper_fill_simulated", created_at=created_at, stage=stage)
    ev["event_id"] = f"evt-fill-{created_at}"
    ev["metrics"] = {
        "fill_quantity": qty,
        "fill_price": price,
        "action": "market_order",
        "submitted_to_broker": False,
    }
    ev["metadata"]["symbol"] = symbol
    ev["metadata"]["sim_fill_flag"] = True
    return ev


class TestFillProjection(unittest.TestCase):
    def _store(self):
        tmp = tempfile.mkdtemp()
        return RuntimeSummaryProjectionStore(path=str(Path(tmp) / "summaries.json"))

    def test_paper_fill_projects_trade_count_last_fill_and_positions(self):
        store = self._store()
        summary = store.project_event(_fill_event())
        self.assertEqual(summary["executed_trade_count"], 1)
        self.assertEqual(summary["total_trades"], 1)
        self.assertEqual(summary["last_fill"]["symbol"], "AAPL.US")
        self.assertEqual(summary["last_fill"]["quantity"], 7.0)
        self.assertEqual(summary["last_fill"]["fill_price"], 100.0)
        self.assertEqual(summary["position_count"], 1)
        self.assertEqual(summary["positions"], [{"symbol": "AAPL.US", "quantity": 7.0}])

    def test_bracket_log_does_not_count_as_an_executed_fill(self):
        store = self._store()
        event = _event(
            event_type="bracket_order_logged",
            created_at="2026-05-01T00:01:00Z",
        )
        event["event_id"] = "evt-bracket-log-only"
        event["metrics"] = {
            "fill_quantity": 7.0,
            "fill_price": 100.0,
            "action": "bracket_logged_only",
            "submitted_to_broker": False,
        }
        event["metadata"]["symbol"] = "AAPL.US"

        summary = store.project_event(event)

        self.assertNotIn("executed_trade_count", summary)
        self.assertNotIn("last_fill", summary)
        self.assertNotIn("positions", summary)

    def test_multiple_fills_accumulate_count_and_positions(self):
        store = self._store()
        store.project_event(_fill_event(qty=7.0, created_at="2026-05-01T00:01:00Z"))
        store.project_event(_fill_event(qty=3.0, created_at="2026-05-01T00:02:00Z"))
        summary = store.project_event(_fill_event(symbol="MSFT.US", qty=5.0, created_at="2026-05-01T00:03:00Z"))
        self.assertEqual(summary["executed_trade_count"], 3)
        self.assertEqual(summary["total_trades"], 3)
        self.assertEqual(summary["last_fill"]["symbol"], "MSFT.US")
        positions = {p["symbol"]: p["quantity"] for p in summary["positions"]}
        self.assertEqual(positions, {"AAPL.US": 10.0, "MSFT.US": 5.0})

    def test_total_trades_metric_does_not_regress_below_executed_count(self):
        store = self._store()
        store.project_event(_fill_event())
        store.project_event(_fill_event(created_at="2026-05-01T00:02:00Z"))
        # a later heartbeat carrying a stale/zero total_trades metric must not lower it
        hb = _event(event_type="heartbeat", created_at="2026-05-01T00:03:00Z")
        hb["metrics"] = {"heartbeat": 1, "total_trades": 0}
        summary = store.project_event(hb)
        # a stale total_trades=0 metric must NOT wipe the executed fill count
        self.assertEqual(summary["executed_trade_count"], 2)
        self.assertEqual(summary["total_trades"], 2)


class TestRuntimeSummaryViewerAccess(unittest.TestCase):
    """
    Validates that:
    1. Evaluator / viewer role admits GET /api/telemetry/runtime-summaries and
       GET /api/telemetry/runtime-summaries/<runtime_id> under strict tenant guard.
    2. Cross-tenant reads are rejected with 403 TENANT_FORBIDDEN.
    3. Other routes (stats, events, ingest) deny viewer role with 403 AUTH_FORBIDDEN.
    """

    @classmethod
    def setUpClass(cls):
        import os
        import time
        from services.runtime_auth_inbound import encode_jwt_hs256
        import services.telemetry.main as telemetry_main

        cls.jwt_secret = "telemetry-test-secret-at-least-32-chars-long!"
        cls._saved_env = {
            "PANTHEON_TELEMETRY_AUTH_MODE": os.environ.get("PANTHEON_TELEMETRY_AUTH_MODE"),
            "PANTHEON_TELEMETRY_JWT_SECRET": os.environ.get("PANTHEON_TELEMETRY_JWT_SECRET"),
            "PANTHEON_TELEMETRY_ALLOWED_TENANTS": os.environ.get("PANTHEON_TELEMETRY_ALLOWED_TENANTS"),
        }
        os.environ["PANTHEON_TELEMETRY_AUTH_MODE"] = "strict"
        os.environ["PANTHEON_TELEMETRY_JWT_SECRET"] = cls.jwt_secret
        os.environ["PANTHEON_TELEMETRY_ALLOWED_TENANTS"] = "tenant-alpha,tenant-dev"

        store = RuntimeSummaryProjectionStore()
        store.project_event({
            "event_id": "evt-seed-dev-1",
            "event_type": "heartbeat",
            "created_at": "2026-05-01T00:00:00Z",
            "deployment_stage": "paper",
            "environment": "paper",
            "binding_id": "rtb-seed-1",
            "runtime_id": "rt-seed-1",
            "capital_pool_id": "pool-seed-1",
            "artifact_id": "artifact-seed-1",
            "artifact_version": "1.0.0",
            "plan_id": "plan-seed-1",
            "persona_capital_binding_id": "pcb-seed-1",
            "tenant_id": "tenant-dev",
            "metrics": {"heartbeat": 1},
            "metadata": {"tenant_id": "tenant-dev"},
        })
        store.project_event({
            "event_id": "evt-seed-retired-1",
            "event_type": "runtime_retired",
            "created_at": "2026-05-01T00:00:00Z",
            "deployment_stage": "paper",
            "environment": "paper",
            "binding_id": "rtb-seed-retired",
            "runtime_id": "rt-seed-retired",
            "capital_pool_id": "pool-seed-2",
            "artifact_id": "artifact-seed-1",
            "artifact_version": "1.0.0",
            "plan_id": "plan-seed-1",
            "persona_capital_binding_id": "pcb-seed-1",
            "tenant_id": "tenant-dev",
            "metrics": {"action": "runtime_retired"},
            "metadata": {"tenant_id": "tenant-dev"},
        })

        if telemetry_main._svc is None:
            class _DummyTelemetrySvc:
                def __init__(self, s):
                    self._runtime_summary_store = s
                def list_runtime_summaries(self, tenant_id=None, include_retired=False):
                    return self._runtime_summary_store.list(
                        tenant_id=tenant_id, include_retired=include_retired
                    )
                def get_runtime_summary(self, runtime_id, tenant_id=None):
                    return self._runtime_summary_store.get(runtime_id, tenant_id=tenant_id)
                def stats(self):
                    return {"status": "ok"}
            telemetry_main._svc = _DummyTelemetrySvc(store)
            cls._did_set_svc = True
        else:
            cls._old_summary_store = getattr(telemetry_main._svc, "_runtime_summary_store", None)
            telemetry_main._svc._runtime_summary_store = store
            cls._did_set_svc = False

        cls.telemetry_main = telemetry_main
        cls.client = telemetry_main.app.test_client()

    @classmethod
    def tearDownClass(cls):
        import os
        for k, v in cls._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if getattr(cls, "_did_set_svc", False):
            cls.telemetry_main._svc = None
        elif hasattr(cls, "_old_summary_store") and cls._old_summary_store is not None:
            cls.telemetry_main._svc._runtime_summary_store = cls._old_summary_store

    def _token(self, roles=None, tenant_id="tenant-dev", allowed_tenants=None, **claims):
        import time
        from services.runtime_auth_inbound import encode_jwt_hs256

        payload = {
            "sub": "telemetry-viewer",
            "roles": roles or ["viewer"],
            "exp": int(time.time()) + 3600,
        }
        if tenant_id is not None:
            payload["tenant_id"] = tenant_id
        if allowed_tenants is not None:
            payload["allowed_tenants"] = allowed_tenants
        payload.update(claims)
        return encode_jwt_hs256(payload, secret=self.jwt_secret)

    def test_viewer_token_reads_runtime_summaries_succeeds(self):
        token = self._token(roles=["viewer"], tenant_id="tenant-dev", allowed_tenants=["tenant-dev"])
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-dev"}

        res_list = self.client.get("/api/telemetry/runtime-summaries", headers=headers)
        self.assertEqual(res_list.status_code, 200, res_list.get_data(as_text=True))
        data = res_list.get_json()
        self.assertGreaterEqual(data["count"], 1)

        res_item = self.client.get("/api/telemetry/runtime-summaries/rt-seed-1", headers=headers)
        self.assertEqual(res_item.status_code, 200)
        self.assertEqual(res_item.get_json()["runtime_id"], "rt-seed-1")

    def test_runtime_summaries_route_excludes_retired_unless_requested(self):
        token = self._token(roles=["viewer"], tenant_id="tenant-dev", allowed_tenants=["tenant-dev"])
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-dev"}

        default = self.client.get("/api/telemetry/runtime-summaries", headers=headers)
        ids = {item["runtime_id"] for item in default.get_json()["summaries"]}
        self.assertIn("rt-seed-1", ids)
        self.assertNotIn("rt-seed-retired", ids)

        explicit = self.client.get(
            "/api/telemetry/runtime-summaries?include_retired=true", headers=headers
        )
        ids = {item["runtime_id"] for item in explicit.get_json()["summaries"]}
        self.assertIn("rt-seed-retired", ids)

    def test_evaluator_viewer_token_reads_runtime_summaries_succeeds(self):
        token = self._token(
            roles=["viewer"],
            tenant_id="tenant-dev",
            allowed_tenants=["tenant-dev"],
            sub="pantheon-dev-persona-evaluator-bff-reader",
            service="pantheon-dev-persona-evaluator-bff-reader",
            scope="pantheon:dev-owner-read",
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Pantheon-Service": "control-plane-bff",
            "X-Tenant-Id": "tenant-dev",
        }
        res_list = self.client.get("/api/telemetry/runtime-summaries", headers=headers)
        self.assertEqual(res_list.status_code, 200)

    def test_viewer_token_cross_tenant_read_rejected(self):
        token = self._token(roles=["viewer"], tenant_id="tenant-dev", allowed_tenants=["tenant-dev"])
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-other"}
        res_cross = self.client.get("/api/telemetry/runtime-summaries", headers=headers)
        self.assertEqual(res_cross.status_code, 403)
        self.assertEqual(res_cross.get_json()["error"]["code"], "TENANT_FORBIDDEN")

    def test_viewer_token_cannot_access_non_summary_routes(self):
        token = self._token(roles=["viewer"], tenant_id="tenant-dev", allowed_tenants=["tenant-dev"])
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-dev"}

        res_stats = self.client.get("/api/telemetry/stats", headers=headers)
        self.assertEqual(res_stats.status_code, 403)
        self.assertEqual(res_stats.get_json()["error"]["code"], "AUTH_FORBIDDEN")

        res_event = self.client.get("/api/telemetry/events/evt-seed-dev-1", headers=headers)
        self.assertEqual(res_event.status_code, 403)
        self.assertEqual(res_event.get_json()["error"]["code"], "AUTH_FORBIDDEN")

    def test_viewer_token_cannot_ingest_telemetry(self):
        token = self._token(roles=["viewer"], tenant_id="tenant-dev", allowed_tenants=["tenant-dev"])
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-dev"}
        res_ingest = self.client.post("/api/telemetry/ingest", headers=headers, json={"event_id": "test"})
        self.assertEqual(res_ingest.status_code, 403)
        self.assertEqual(res_ingest.get_json()["error"]["code"], "AUTH_FORBIDDEN")



class TestRetirementEventDeliveryThroughRealAuth(unittest.TestCase):
    """runtime-manager emitter -> telemetry ingest route (real auth path)."""

    TOKEN = "telemetry-service-secret"

    def setUp(self):
        import os
        from unittest.mock import patch

        import services.telemetry.main as telemetry_main

        self.store = RuntimeSummaryProjectionStore()
        outer = self

        from services.telemetry.ingest_svc import TelemetryIngestService

        schema_path = Path(telemetry_main.__file__).parent / "telemetry_event.schema.json"
        _Svc = TelemetryIngestService(
            schema_path=str(schema_path),
            buffer_backend="memory",
            runtime_summary_store=self.store,
        )

        self._saved_svc = telemetry_main._svc
        telemetry_main._svc = _Svc
        self.addCleanup(setattr, telemetry_main, "_svc", self._saved_svc)
        run_async = patch.object(
            telemetry_main, "_run_async", lambda coro: __import__("asyncio").run(coro)
        )
        run_async.start()
        self.addCleanup(run_async.stop)
        env = patch.dict(
            os.environ,
            {
                "PANTHEON_TELEMETRY_SERVICE_TOKEN": self.TOKEN,
                "PANTHEON_TELEMETRY_SERVICE_TENANTS": "tenant-dev",
                "PANTHEON_TELEMETRY_AUTH_MODE": "strict",
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.client = telemetry_main.app.test_client()

    def _sender(self, tenant_id, fail_times=0):
        from services.runtime_manager.retirement_telemetry import HttpTelemetrySender

        outer = self
        state = {"calls": 0, "statuses": []}

        class _Sender(HttpTelemetrySender):
            def __call__(self, event):
                state["calls"] += 1
                if state["calls"] <= fail_times:
                    return False  # simulated lost delivery (service restarting)
                resp = outer.client.post(
                    "/api/telemetry/ingest",
                    json=event,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "X-Tenant-Id": self._tenant_id,
                    },
                )
                state["statuses"].append(resp.status_code)
                return resp.status_code in {200, 202}

        sender = _Sender("http://telemetry", token=self.TOKEN, tenant_id=tenant_id)
        return sender, state

    def _service(self, sender, tmp):
        from services.runtime_manager.service import RuntimeManagerService

        return RuntimeManagerService(
            store_path=Path(tmp) / "bindings.json",
            retirement_telemetry_sender=sender,
        )

    def _binding(self, binding_id="rtb-aaa000000001", runtime_id="rt-lean-001", **kw):
        from services.runtime_manager.test_runtime_binding import _base

        return _base(binding_id=binding_id, runtime_id=runtime_id, **kw)

    def _retired_ids(self, tenant="tenant-dev"):
        return {
            s["runtime_id"]
            for s in self.store.list(tenant_id=tenant, include_retired=True)
            if s.get("status") == "retired" or s.get("retired")
        }

    def test_retirement_lands_in_binding_tenant_and_is_acked(self):
        sender, state = self._sender("tenant-dev")
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(sender, tmp)
            svc.store.create(self._binding())
            svc.retire("rtb-aaa000000001", retired_at="2026-05-16T01:00:00Z")
            meta = svc.store.get("rtb-aaa000000001").metadata
        self.assertEqual(state["statuses"], [202])
        self.assertTrue(meta["retirement_telemetry_acked_at"])
        ids = {s["runtime_id"] for s in self.store.list(tenant_id="tenant-dev")}
        self.assertNotIn("rt-lean-001", ids)
        explicit = {
            s["runtime_id"]
            for s in self.store.list(tenant_id="tenant-dev", include_retired=True)
        }
        self.assertIn("rt-lean-001", explicit)

    def test_wrong_tenant_is_rejected_and_not_acked(self):
        sender, state = self._sender("tenant-other")
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(sender, tmp)
            svc.store.create(self._binding())
            svc.retire("rtb-aaa000000001", retired_at="2026-05-16T01:00:00Z")
            meta = svc.store.get("rtb-aaa000000001").metadata
        self.assertEqual(state["statuses"], [403])
        self.assertNotIn("retirement_telemetry_acked_at", meta)
        self.assertEqual(self.store.list(tenant_id="tenant-other", include_retired=True), [])

    def test_lost_delivery_is_recovered_by_sweep(self):
        sender, state = self._sender("tenant-dev", fail_times=1)
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(sender, tmp)
            svc.store.create(self._binding())
            svc.retire("rtb-aaa000000001", retired_at="2026-05-16T01:00:00Z")
            self.assertNotIn(
                "retirement_telemetry_acked_at",
                svc.store.get("rtb-aaa000000001").metadata,
            )
            self.assertEqual(svc.store.list_all()[0].status, "retired")
            self.assertEqual(svc._retirement_emitter.sweep(), 1)
            self.assertEqual(svc._retirement_emitter.sweep(), 0)  # acked: no resend
        self.assertEqual(state["calls"], 2)
        self.assertEqual(state["statuses"], [202])

    def test_startup_backfill_of_already_retired_binding(self):
        sender, state = self._sender("tenant-dev")
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "bindings.json"
            from services.runtime_manager.runtime_binding import RuntimeBindingStore

            legacy = RuntimeBindingStore(path=store_path)  # no observer: pre-emitter era
            legacy.create(self._binding())
            legacy.retire("rtb-aaa000000001", retired_at="2026-05-16T01:00:00Z")
            self.assertEqual(state["calls"], 0)

            svc = self._service(sender, tmp)  # restart with persisted retired binding
            self.assertEqual(len(svc._retirement_emitter.pending()), 1)
            self.assertEqual(svc._retirement_emitter.sweep(), 1)
            self.assertEqual(svc._retirement_emitter.pending(), [])
        self.assertEqual(state["statuses"], [202])
        self.assertEqual(self._retired_ids(), {"rt-lean-001"})

    def test_sweep_is_bounded(self):
        sender, state = self._sender("tenant-dev")
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(None, tmp)
            for i in range(3):
                svc.store.create(
                    self._binding(f"rtb-aaa00000000{i + 1}", f"rt-lean-00{i + 1}")
                )
                svc.retire(f"rtb-aaa00000000{i + 1}", retired_at="2026-05-16T01:00:00Z")
            from services.runtime_manager.retirement_telemetry import RetirementTelemetryEmitter

            emitter = RetirementTelemetryEmitter(sender, store=svc.store)
            self.assertEqual(emitter.sweep(limit=2), 2)
            self.assertEqual(emitter.sweep(limit=2), 1)


def _valuation_event(
    marks,
    *,
    event_type="pnl_snapshot",
    created_at="2026-10-21T02:36:09Z",
    binding_id="rtb-paper-001",
    event_id=None,
):
    ev = _event(event_type=event_type, created_at=created_at)
    ev["event_id"] = event_id or f"evt-val-{event_type}-{created_at}-{binding_id}"
    ev["binding_id"] = binding_id
    ev["metrics"] = {"portfolio_value": 1.0, "pnl": 0.5, "drawdown_pct": 0.0}
    ev["metadata"].update(
        {
            "valuation_method": "fill_cash_ledger_mark_to_market",
            "valuation_as_of": "2026-10-20T00:00:00Z",
            "mark_refs": marks,
        }
    )
    return ev


_RECORDED_2330_CALENDAR_EVIDENCE = {
    "authority": "Taiwan Stock Exchange 115 年10月23日休市公告 (2026-10-22)",
    "checksum": "48e8d89449b436d936cb4a25c9860141b11a838d4f873deb2717af9fd896df99",
    "coverage_end": "2026-10-28",
    "coverage_start": "2026-10-22",
    "fetched_at": "2026-10-23T07:00:39Z",
    "holidays": {
        "2026-10-23": {
            "name": "颱風全日休市 (TWSE 臨時休市公告)",
        },
        "2026-10-25": {
            "name": "臺灣光復暨金門古寧頭大捷紀念日",
        },
        "2026-10-26": {
            "name": "臺灣光復暨金門古寧頭大捷紀念日",
        },
    },
    "market": "TW",
    "source_url": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6a0fa351b01a1638507940202.pdf",
    "timezone": "Asia/Taipei",
    "trading_days": [
        "2026-10-22",
        "2026-10-27",
        "2026-10-28",
    ],
    "venue": "TWSE",
    "version": "twse-2026-emergency-closure-20261023-v2",
}

_RECORDED_2330_LINEAGE = {
    "source_ids": [
        "tw-official:tw_price_daily:TWSE:2330:9691b8ad1b066a80",
    ],
    "connector_ids": [
        "tw-twse-tpex-official-market",
    ],
    "content_refs": [
        "tw-official://tw_price_daily/TWSE/2330/2026-10-22/9691b8ad1b066a80",
    ],
    "ingest_run_ids": [
        "ingest-e53a86463995",
    ],
}


def _default_tw_observed_at(as_of: str) -> str:
    if isinstance(as_of, str) and len(as_of) >= 10 and as_of.endswith("T00:00:00Z"):
        return f"{as_of[:10]}T06:00:00Z"
    return str(as_of)


def _mark(symbol="2330.TW", price=2405.0, as_of="2026-10-20T00:00:00Z", **extra):
    base_symbol = str(symbol or "").split(".", 1)[0]
    ref = {
        "symbol": symbol,
        "price": price,
        "as_of": as_of,
        "source_ref": "source-ingest://snapshots/mss-44f01ca7b8891e2be3fb721f",
    }
    if str(symbol or "").upper().endswith((".TW", ".TWO")):
        ref["observed_at"] = _default_tw_observed_at(as_of)
        ref["lineage"] = {
            "source_ids": [f"tw-official:tw_price_daily:TWSE:{base_symbol}:test"],
            "connector_ids": ["tw-twse-tpex-official-market"],
        }
    ref.update(extra)
    return ref


class TestValuationMarkProjection(unittest.TestCase):
    def _store(self):
        tmp = tempfile.mkdtemp()
        return RuntimeSummaryProjectionStore(path=str(Path(tmp) / "summaries.json"))

    def _short_fill(self, store):
        return store.project_event(_fill_event(symbol="2330.TW", qty=-1.0, price=2400.0))

    def test_fill_only_position_has_no_mark(self):
        summary = self._short_fill(self._store())
        self.assertEqual(summary["positions"], [{"symbol": "2330.TW", "quantity": -1.0}])

    def test_snapshot_marks_signed_position_with_quote_asof_not_event_time(self):
        store = self._store()
        self._short_fill(store)
        summary = store.project_event(_valuation_event([_mark()]))
        self.assertEqual(
            summary["positions"],
            [
                {
                    "symbol": "2330.TW",
                    "quantity": -1.0,
                    "mark_price": 2405.0,
                    "mark_as_of": "2026-10-20T00:00:00Z",
                    "mark_source_ref": "source-ingest://snapshots/mss-44f01ca7b8891e2be3fb721f",
                    "market_value": -2405.0,
                    "mark_observed_at": "2026-10-20T06:00:00Z",
                    "mark_lineage": {
                        "source_ids": ["tw-official:tw_price_daily:TWSE:2330:test"],
                        "connector_ids": ["tw-twse-tpex-official-market"],
                    },
                }
            ],
        )
        self.assertNotIn("currency", summary["positions"][0])

    def test_quote_currency_only_when_supplied(self):
        store = self._store()
        self._short_fill(store)
        summary = store.project_event(_valuation_event([_mark(quote_currency="TWD")]))
        self.assertEqual(summary["positions"][0]["currency"], "TWD")

    def test_heartbeat_does_not_refresh_mark_asof(self):
        store = self._store()
        self._short_fill(store)
        store.project_event(_valuation_event([_mark()]))
        hb = _event("heartbeat", created_at="2026-10-21T05:00:00Z")
        summary = store.project_event(hb)
        self.assertEqual(summary["positions"][0]["mark_as_of"], "2026-10-20T00:00:00Z")

    def test_mark_before_fill_still_joins_later_fill(self):
        store = self._store()
        store.project_event(_valuation_event([_mark()]))
        summary = self._short_fill(store)
        self.assertEqual(summary["positions"][0]["market_value"], -2405.0)

    def test_older_or_duplicate_mark_does_not_regress_accepted_mark(self):
        store = self._store()
        self._short_fill(store)
        store.project_event(_valuation_event([_mark(price=2405.0, as_of="2026-10-20T00:00:00Z")], event_id="a"))
        store.project_event(_valuation_event([_mark(price=2300.0, as_of="2026-10-19T00:00:00Z")], event_id="b"))
        summary = store.project_event(
            _valuation_event([_mark(price=1.0, as_of="2026-10-20T00:00:00Z")], event_type="drawdown_snapshot", event_id="c")
        )
        self.assertEqual(summary["positions"][0]["mark_price"], 2405.0)
        summary = store.project_event(
            _valuation_event(
                [_mark(price=2410.0, as_of="2026-10-21T00:00:00Z")],
                created_at="2026-10-21T06:30:00Z",
                event_id="d",
            )
        )
        self.assertEqual(summary["positions"][0]["mark_price"], 2410.0)
        self.assertEqual(summary["positions"][0]["mark_as_of"], "2026-10-21T00:00:00Z")

    def test_cross_symbol_mark_does_not_contaminate_position(self):
        store = self._store()
        self._short_fill(store)
        summary = store.project_event(_valuation_event([_mark(symbol="2317.TW", price=99.0)]))
        self.assertEqual(summary["positions"], [{"symbol": "2330.TW", "quantity": -1.0}])

    def test_invalid_or_missing_marks_stay_unavailable(self):
        store = self._store()
        self._short_fill(store)
        bad = [
            _mark(price=0),
            _mark(price=-5.0),
            _mark(price=True),
            _mark(price=float("nan")),
            _mark(as_of="2026-10-20T00:00:00"),
            {"symbol": "2330.TW", "price": 1.0, "as_of": "2026-10-20T00:00:00Z"},
        ]
        summary = store.project_event(_valuation_event(bad))
        self.assertEqual(summary["positions"], [{"symbol": "2330.TW", "quantity": -1.0}])
        summary = store.project_event(_valuation_event([], event_id="empty"))
        self.assertEqual(summary["positions"], [{"symbol": "2330.TW", "quantity": -1.0}])

    def test_binding_rollover_drops_old_binding_marks(self):
        store = self._store()
        self._short_fill(store)
        store.project_event(_valuation_event([_mark()]))
        new = _event("heartbeat", created_at="2026-10-21T10:00:00Z")
        new.update({"event_id": "evt-new", "binding_id": "rtb-paper-002"})
        new["metadata"]["runtime_binding_effective_at"] = "2026-10-21T09:59:00Z"
        summary = store.project_event(new)
        self.assertEqual(summary["binding_id"], "rtb-paper-002")
        self.assertEqual(summary.get("positions", []), [])
        self.assertNotIn("_marks_by_symbol", summary)

    def test_derived_metric_echo_does_not_carry_marks(self):
        store = self._store()
        self._short_fill(store)
        ev = _valuation_event([_mark()])
        ev["metadata"]["derived_from_threshold_evaluation"] = True
        summary = store.project_event(ev)
        self.assertEqual(summary["positions"], [{"symbol": "2330.TW", "quantity": -1.0}])


class TestValuationFailureProjection(unittest.TestCase):
    def _store(self):
        tmp = tempfile.mkdtemp()
        store = RuntimeSummaryProjectionStore(path=str(Path(tmp) / "summaries.json"))
        store.project_event(_fill_event(symbol="2330.TW", qty=-1.0, price=2400.0))
        store.project_event(_valuation_event([_mark()]))
        return store

    def _perf_heartbeat(self, at, status="marks_unavailable", **extra):
        hb = _event("heartbeat", created_at=at)
        hb["event_id"] = f"evt-hb-{at}-{status}"
        hb["metadata"]["performance_telemetry"] = {
            "status": status,
            "code": "stale_or_future_market_marks",
            "attempted_at": at,
            **extra,
        }
        return hb

    def test_failed_valuation_heartbeat_withdraws_mark_but_keeps_last_known(self):
        store = self._store()
        summary = store.project_event(self._perf_heartbeat("2026-10-21T04:00:00Z"))
        (row,) = summary["positions"]
        self.assertNotIn("mark_price", row)
        self.assertNotIn("market_value", row)
        self.assertEqual(row["mark_unavailable_reason"], "stale_or_future_market_marks")
        self.assertEqual(row["last_known_mark_price"], 2405.0)
        self.assertEqual(row["last_known_mark_as_of"], "2026-10-20T00:00:00Z")

    def test_older_failure_heartbeat_after_recovery_is_ignored(self):
        store = self._store()
        store.project_event(self._perf_heartbeat("2026-10-21T04:00:00Z"))
        store.project_event(self._perf_heartbeat("2026-10-21T05:00:00Z", status="valued"))
        summary = store.project_event(self._perf_heartbeat("2026-10-21T04:30:00Z"))
        self.assertEqual(summary["positions"][0]["mark_price"], 2405.0)

    def test_later_snapshot_clears_failure_but_older_event_does_not(self):
        store = self._store()
        store.project_event(self._perf_heartbeat("2026-10-21T09:00:00Z"))
        summary = store.project_event(
            _valuation_event(
                [_mark(price=2410.0, as_of="2026-10-21T08:00:00Z")],
                created_at="2026-10-21T08:30:00Z",
                event_id="old-event",
            )
        )
        self.assertNotIn("mark_price", summary["positions"][0])
        summary = store.project_event(
            _valuation_event(
                [_mark(price=2420.0, as_of="2026-10-21T09:59:00Z")],
                created_at="2026-10-21T10:00:00Z",
                event_id="new-event",
            )
        )
        self.assertEqual(summary["positions"][0]["mark_price"], 2420.0)

    def test_failure_scoped_to_missing_symbols(self):
        store = self._store()
        store.project_event(_fill_event(symbol="2317.TW", qty=1.0, price=100.0))
        store.project_event(_valuation_event([_mark(symbol="2317.TW", price=101.0)], event_id="o"))
        summary = store.project_event(
            self._perf_heartbeat("2026-10-21T04:00:00Z", missing_symbols=["2330.TW"])
        )
        by_symbol = {row["symbol"]: row for row in summary["positions"]}
        self.assertNotIn("mark_price", by_symbol["2330.TW"])
        self.assertEqual(by_symbol["2317.TW"]["mark_price"], 101.0)

    def test_plain_heartbeat_after_mark_expiration_marks_position_stale(self):
        tmp = tempfile.mkdtemp()
        store = RuntimeSummaryProjectionStore(path=str(Path(tmp) / "summaries.json"))
        store.project_event(_fill_event(symbol="2330.TW", qty=-1.0, price=2400.0))
        store.project_event(
            _valuation_event(
                [_mark(price=2405.0, as_of="2026-10-15T00:00:00Z")],
                created_at="2026-10-15T06:05:00Z",
            )
        )
        hb = _event("heartbeat", created_at="2026-10-21T06:00:00Z")
        hb["event_id"] = "evt-hb-6d-later"
        summary = store.project_event(hb)
        (row,) = summary["positions"]
        self.assertNotIn("mark_price", row)
        self.assertNotIn("market_value", row)
        self.assertTrue(row["mark_stale"])
        self.assertEqual(row["last_known_mark_price"], 2405.0)
        self.assertEqual(row["last_known_mark_as_of"], "2026-10-15T00:00:00Z")
        self.assertEqual(
            row["last_known_mark_source_ref"],
            "source-ingest://snapshots/mss-44f01ca7b8891e2be3fb721f",
        )

    def test_out_of_order_failure_heartbeat_before_newer_snapshot_is_ignored(self):
        store = self._store()
        store.project_event(
            _valuation_event(
                [_mark(price=2420.0, as_of="2026-10-21T09:59:00Z")],
                created_at="2026-10-21T10:00:00Z",
                event_id="snap-10",
            )
        )
        summary = store.project_event(self._perf_heartbeat("2026-10-21T09:30:00Z"))
        self.assertEqual(summary["positions"][0]["mark_price"], 2420.0)

    def test_h12_76062_owner_dto_governed_calendar_enrichment_and_negatives(self):
        tmp = tempfile.mkdtemp()
        store = RuntimeSummaryProjectionStore(path=str(Path(tmp) / "summaries.json"))
        binding_id = "rb-eea1cd16cf3745479f42e8b4440b3c27"
        runtime_id = "rt-eb295fe2"
        source_ref = "source-ingest://snapshots/mss-ff49b0e9e567bba38063946a"

        def _h12_event(event_type, created_at, *, event_id, metrics=None, metadata=None, b_id=binding_id, r_id=runtime_id):
            ev = _event(event_type=event_type, created_at=created_at)
            ev["event_id"] = event_id
            ev["runtime_id"] = r_id
            ev["binding_id"] = b_id
            ev["metrics"] = dict(metrics or {})
            ev["metadata"].update(metadata or {})
            return ev

        # 1. Reproduce pre-fix 76062 state: fill of -2.0 on 2330.TW, mark_refs without provenance,
        # and later heartbeat on 2026-10-25T21:41:42Z results in mark_stale=True.
        store.project_event(
            _h12_event(
                "paper_fill_simulated",
                "2026-10-20T02:27:49Z",
                event_id="evt-h12-fill-1",
                metrics={"fill_quantity": -2.0, "fill_price": 2400.0, "action": "market_order"},
                metadata={"symbol": "2330.TW", "sim_fill_flag": True},
            )
        )
        store.project_event(
            _h12_event(
                "pnl_snapshot",
                "2026-10-22T06:00:00Z",
                event_id="evt-h12-pnl-legacy",
                metrics={"pnl": 30.0, "portfolio_value": 100030.0},
                metadata={
                    "valuation_method": "fill_cash_ledger_mark_to_market",
                    "valuation_as_of": "2026-10-22T00:00:00Z",
                    "mark_refs": [
                        {
                            "symbol": "2330.TW",
                            "price": 2385.0,
                            "as_of": "2026-10-22T00:00:00Z",
                            "source_ref": source_ref,
                        }
                    ],
                },
            )
        )
        legacy_summary = store.project_event(
            _h12_event(
                "heartbeat",
                "2026-10-25T21:41:42Z",
                event_id="evt-h12-hb-oct25",
                metrics={"heartbeat": 1},
            )
        )
        self.assertEqual(
            legacy_summary["positions"],
            [
                {
                    "symbol": "2330.TW",
                    "quantity": -2.0,
                    "mark_stale": True,
                    "last_known_mark_price": 2385.0,
                    "last_known_mark_as_of": "2026-10-22T00:00:00Z",
                    "last_known_mark_source_ref": source_ref,
                }
            ],
        )

        # 2. Reject invalid same-as_of enrichment attempts (different price, different source_ref,
        # untrusted pin, non-official lineage, malformed calendar).
        for bad_case, bad_ref in (
            (
                "different_price",
                {
                    "symbol": "2330.TW",
                    "price": 9999.0,
                    "as_of": "2026-10-22T00:00:00Z",
                    "source_ref": source_ref,
                    "observed_at": "2026-10-23T12:29:54Z",
                    "lineage": _RECORDED_2330_LINEAGE,
                    "calendar_evidence": _RECORDED_2330_CALENDAR_EVIDENCE,
                },
            ),
            (
                "different_source",
                {
                    "symbol": "2330.TW",
                    "price": 2385.0,
                    "as_of": "2026-10-22T00:00:00Z",
                    "source_ref": "source-ingest://snapshots/other",
                    "observed_at": "2026-10-23T12:29:54Z",
                    "lineage": _RECORDED_2330_LINEAGE,
                    "calendar_evidence": _RECORDED_2330_CALENDAR_EVIDENCE,
                },
            ),
            (
                "untrusted_pin",
                {
                    "symbol": "2330.TW",
                    "price": 2385.0,
                    "as_of": "2026-10-22T00:00:00Z",
                    "source_ref": source_ref,
                    "observed_at": "2026-10-23T12:29:54Z",
                    "lineage": _RECORDED_2330_LINEAGE,
                    "calendar_evidence": {
                        **_RECORDED_2330_CALENDAR_EVIDENCE,
                        "version": "twse-2026-untrusted-v99",
                    },
                },
            ),
            (
                "non_official_lineage",
                {
                    "symbol": "2330.TW",
                    "price": 2385.0,
                    "as_of": "2026-10-22T00:00:00Z",
                    "source_ref": source_ref,
                    "observed_at": "2026-10-23T12:29:54Z",
                    "lineage": {"source_ids": ["yahoo:2330"], "connector_ids": ["yahoo"]},
                    "calendar_evidence": _RECORDED_2330_CALENDAR_EVIDENCE,
                },
            ),
            (
                "malformed_calendar",
                {
                    "symbol": "2330.TW",
                    "price": 2385.0,
                    "as_of": "2026-10-22T00:00:00Z",
                    "source_ref": source_ref,
                    "observed_at": "2026-10-23T12:29:54Z",
                    "lineage": _RECORDED_2330_LINEAGE,
                    "calendar_evidence": "not-a-dict",
                },
            ),
        ):
            with self.subTest(bad_case=bad_case):
                rejected_summary = store.project_event(
                    _h12_event(
                        "pnl_snapshot",
                        "2026-10-25T21:42:00Z",
                        event_id=f"evt-h12-bad-{bad_case}",
                        metrics={"pnl": 30.0, "portfolio_value": 100030.0},
                        metadata={"mark_refs": [bad_ref]},
                    )
                )
                self.assertTrue(rejected_summary["positions"][0].get("mark_stale"))
                self.assertNotIn("mark_price", rejected_summary["positions"][0])

        # 3. Same-as_of unchanged-sample snapshot carrying genuine governed calendar evidence
        # enriches _marks_by_symbol and projects active mark_price/market_value without changing as_of.
        valid_gov_ref = {
            "symbol": "2330.TW",
            "price": 2385.0,
            "as_of": "2026-10-22T00:00:00Z",
            "source_ref": source_ref,
            "observed_at": "2026-10-23T12:29:54Z",
            "lineage": _RECORDED_2330_LINEAGE,
            "calendar_evidence": _RECORDED_2330_CALENDAR_EVIDENCE,
        }
        enriched_summary = store.project_event(
            _h12_event(
                "pnl_snapshot",
                "2026-10-25T21:43:00Z",
                event_id="evt-h12-pnl-gov",
                metrics={"pnl": 30.0, "portfolio_value": 100030.0},
                metadata={
                    "valuation_method": "fill_cash_ledger_mark_to_market",
                    "valuation_as_of": "2026-10-22T00:00:00Z",
                    "mark_refs": [valid_gov_ref],
                },
            )
        )
        (pos,) = enriched_summary["positions"]
        self.assertNotIn("mark_stale", pos)
        self.assertEqual(pos["symbol"], "2330.TW")
        self.assertEqual(pos["quantity"], -2.0)
        self.assertEqual(pos["mark_price"], 2385.0)
        self.assertEqual(pos["market_value"], -4770.0)
        self.assertEqual(pos["mark_as_of"], "2026-10-22T00:00:00Z")
        self.assertEqual(pos["mark_source_ref"], source_ref)
        self.assertEqual(pos["mark_observed_at"], "2026-10-23T12:29:54Z")
        self.assertEqual(pos["mark_lineage"], _RECORDED_2330_LINEAGE)
        self.assertEqual(pos["mark_calendar_evidence"], _RECORDED_2330_CALENDAR_EVIDENCE)

        # 4. Subsequent drawdown_snapshot with identical evidence is idempotent;
        # regression attempts (older observed_at or dropped calendar_evidence) do not regress it.
        dup_summary = store.project_event(
            _h12_event(
                "drawdown_snapshot",
                "2026-10-25T21:43:00Z",
                event_id="evt-h12-dd-gov",
                metrics={"drawdown_pct": 0.0, "portfolio_value": 100030.0},
                metadata={"mark_refs": [valid_gov_ref]},
            )
        )
        self.assertEqual(dup_summary["positions"], enriched_summary["positions"])

        regress_no_cal = dict(valid_gov_ref)
        regress_no_cal.pop("calendar_evidence")
        after_regress = store.project_event(
            _h12_event(
                "pnl_snapshot",
                "2026-10-25T21:44:00Z",
                event_id="evt-h12-regress-no-cal",
                metrics={"pnl": 30.0},
                metadata={"mark_refs": [regress_no_cal]},
            )
        )
        self.assertEqual(after_regress["positions"][0]["mark_price"], 2385.0)
        self.assertEqual(
            after_regress["positions"][0]["mark_calendar_evidence"]["version"],
            "twse-2026-emergency-closure-20261023-v2",
        )

        # 5. Missed trading session after Tuesday 2026-10-27 13:30 Asia/Taipei close (05:30Z)
        # marks the Oct 22 quote stale even with governed holiday calendar.
        after_tuesday_close = store.project_event(
            _h12_event(
                "heartbeat",
                "2026-10-27T05:31:00Z",
                event_id="evt-h12-hb-oct27-after-close",
                metrics={"heartbeat": 1},
            )
        )
        (stale_pos,) = after_tuesday_close["positions"]
        self.assertTrue(stale_pos["mark_stale"])
        self.assertNotIn("mark_price", stale_pos)
        self.assertNotIn("market_value", stale_pos)
        self.assertEqual(stale_pos["last_known_mark_as_of"], "2026-10-22T00:00:00Z")
        self.assertEqual(stale_pos["last_known_mark_observed_at"], "2026-10-23T12:29:54Z")


class TestCommittedCloseFinancialConsistency(unittest.TestCase):
    """Runtime ledger fill_count must govern whether fill-derived positions are current."""

    def _store(self):
        return RuntimeSummaryProjectionStore(
            path=str(Path(tempfile.mkdtemp()) / "summaries.json")
        )

    @staticmethod
    def _snapshot(event_type, *, created_at, fill_count, pnl=-15.0, drawdown=0.00015):
        event = _event(event_type, created_at=created_at)
        event["event_id"] = f"evt-{event_type}-{created_at}"
        event["metrics"] = {
            "pnl": pnl,
            "drawdown_pct": drawdown,
            "portfolio_value": 100000.0 + pnl,
            "initial_cash": 100000.0,
            "cash": 99985.0,
            "fill_count": fill_count,
        }
        return event

    def _open_short(self, store):
        store.project_event(
            _fill_event(symbol="2330.TW", qty=-1.0, price=2385.0, created_at="2026-10-27T01:43:10Z")
        )

    def test_missing_close_fill_event_withholds_stale_open_position(self):
        store = self._store()
        self._open_short(store)
        summary = store.project_event(
            self._snapshot("pnl_snapshot", created_at="2026-10-27T13:03:00Z", fill_count=2)
        )
        summary = store.project_event(
            self._snapshot("drawdown_snapshot", created_at="2026-10-27T13:03:00Z", fill_count=2)
        )
        self.assertEqual(summary["executed_trade_count"], 1)
        self.assertEqual(summary["ledger_fill_count"], 2)
        self.assertEqual(summary["positions"], [])
        self.assertIsNone(summary["position_count"])
        self.assertEqual(summary["positions_status"], "unavailable")
        self.assertIn("fill_event_gap", summary["positions_unavailable_reason"])
        # Authoritative ledger financials are still presented.
        self.assertEqual(summary["pnl"], -15.0)
        self.assertEqual(summary["drawdown"], 0.00015)

    def test_close_fill_event_restores_flat_position_and_clears_unavailability(self):
        store = self._store()
        self._open_short(store)
        store.project_event(
            self._snapshot("pnl_snapshot", created_at="2026-10-27T13:03:00Z", fill_count=2)
        )
        summary = store.project_event(
            _fill_event(symbol="2330.TW", qty=1.0, price=2400.0, created_at="2026-10-27T13:01:35Z")
        )
        self.assertEqual(summary["executed_trade_count"], 2)
        self.assertEqual(summary["positions"], [])
        self.assertEqual(summary["position_count"], 0)
        self.assertNotIn("positions_status", summary)
        self.assertEqual(summary["pnl"], -15.0)

    def test_unchanged_heartbeat_fill_count_upgrades_stored_summary(self):
        # Historical shape: stored summary has fill1/short1 and the runtime
        # ledger is flat and unchanged, so no new pnl/drawdown snapshot exists.
        store = self._store()
        self._open_short(store)
        heartbeat = _event("heartbeat", created_at="2026-10-27T13:05:00Z")
        heartbeat["event_id"] = "evt-hb-unchanged"
        heartbeat["metadata"] = {
            **(heartbeat.get("metadata") or {}),
            "performance_telemetry": {
                "status": "unchanged",
                "code": "performance_sample_unchanged",
                "fill_count": 2,
            },
        }
        summary = store.project_event(heartbeat)
        self.assertEqual(summary["ledger_fill_count"], 2)
        self.assertEqual(summary["positions"], [])
        self.assertIsNone(summary["position_count"])
        self.assertEqual(summary["positions_status"], "unavailable")
        self.assertIn("fill_event_gap", summary["positions_unavailable_reason"])
        # No fabricated fill or financials.
        self.assertEqual(summary["executed_trade_count"], 1)
        self.assertNotIn("pnl", summary)

    def test_consistent_fill_count_does_not_flag_positions(self):
        store = self._store()
        self._open_short(store)
        summary = store.project_event(
            self._snapshot("pnl_snapshot", created_at="2026-10-27T01:44:00Z", fill_count=1, pnl=0.0, drawdown=0.0)
        )
        self.assertNotIn("positions_status", summary)
        self.assertEqual(summary["position_count"], 1)

    def test_missing_financial_inputs_stay_unavailable_not_zero(self):
        store = self._store()
        summary = store.project_event(_event("heartbeat", created_at="2026-10-27T13:03:00Z"))
        for key in ("pnl", "drawdown", "sharpe_ratio"):
            self.assertNotIn(key, summary)
        self.assertNotIn("ledger_fill_count", summary)


# Frozen real inputs (read-only owner GETs, tenant-dev paper, 2026-10-27).
#   summary  <- financial-inputs-after-d551-release-20261028.json
#               sha256 d113f567ea3d3f39596c90bb3649005b3440832d943fbf0aa92b917d3bc0d3a5
#               (financial_owner_read.summaries[0], runtime rt-81e2f12f, d551 projector)
#   heartbeat <- financial-original-heartbeat-owner-proof-20261028.json
#               sha256 2bb54a980565569eaf7abeb1eadf0ee065b3e2e6e41f39c798e9d15ad337898f
#               (event cb71a62f-d073-496c-82b4-336cf1b50b6b)
# Persisted ledger (sha256 a8929fb5...a0a32): fill_count=2, holdings={}, cash=99985.
_REAL_D551_SUMMARY = json.loads(r"""
{
    "_binding_boundary_at": "2026-10-27T01:42:12Z",
    "_binding_boundary_source": "metadata.runtime_binding_effective_at",
    "_binding_effective_at": "2026-10-27T01:42:12Z",
    "_binding_effective_source": "metadata.runtime_binding_effective_at",
    "_binding_first_event_at": "2026-10-27T01:42:26Z",
    "_binding_latest_event_at": "2026-10-27T19:54:11Z",
    "_marks_by_symbol": {
        "2330.TW": {
            "as_of": "2026-10-27T00:00:00Z",
            "binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
            "calendar_evidence": {
                "authority": "Taiwan Stock Exchange 115 年10月23日休市公告 (2026-10-22)",
                "checksum": "48e8d89449b436d936cb4a25c9860141b11a838d4f873deb2717af9fd896df99",
                "coverage_end": "2026-10-28",
                "coverage_start": "2026-10-22",
                "fetched_at": "2026-10-23T07:00:39Z",
                "holidays": {
                    "2026-10-23": {
                        "name": "颱風全日休市 (TWSE 臨時休市公告)"
                    },
                    "2026-10-25": {
                        "name": "臺灣光復暨金門古寧頭大捷紀念日"
                    },
                    "2026-10-26": {
                        "name": "臺灣光復暨金門古寧頭大捷紀念日"
                    }
                },
                "market": "TW",
                "source_url": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6a0fa351b01a1638507940202.pdf",
                "timezone": "Asia/Taipei",
                "trading_days": [
                    "2026-10-22",
                    "2026-10-27",
                    "2026-10-28"
                ],
                "venue": "TWSE",
                "version": "twse-2026-emergency-closure-20261023-v2"
            },
            "lineage": {
                "connector_ids": [
                    "tw-twse-tpex-official-market"
                ],
                "content_refs": [
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-12/b7cccaadeb0de836",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-13/fa96f9c7d494cd4b",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-14/3fdfa78a63cc6d7d",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-17/be1c4fec958d482d",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-18/6f629e3bdaa4d52b",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-19/5257e4b094f61680",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-20/4d265a41afde9033",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-21/c15db3967fe11de8",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-24/3521c8e4c5014967",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-25/ae55e73ad36f33d5",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-26/34cb2f6fafcb1fb9",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-27/58e5badefcb24b26",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-28/8908237b6ac95ed9",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-31/9ce71bc7f3123ad9",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-01/6450635b77534ef3",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-02/7e3e16125d62c8e2",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-03/11d3e2b9da811052",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-04/c8babce8ba646574",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-07/469fa93787137ead",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-08/820530d545bb5c38",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-09/ea38784557eda681",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-10/a63257be0d676e8a",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-11/1d6dab1e84b80fdb",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-14/f68a82442a25756c",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-15/0b4d1f4c49e30fd9",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-16/0a8ac12f9dd41fe4",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-17/37806df652ed08ad",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-18/bf5f15a59e19ad0a",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-21/fc286d5a7640de26",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-22/e759774fff2be04d",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-23/238532f02e22165f",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-24/05bca2967dc31336",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-29/523257279b4c61f2",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-30/47104356c7fb192a",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-01/53fc49a2eabe1a60",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-02/53197c0eb80d9a79",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-05/94715490491f5d22",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-06/e961087e0abb112e",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-07/baa9a905ee4c2e18",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-08/d431e8abd8d642af",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-12/87f48a6834d99cb2",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-13/a2eb8eb547ae72d5",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-14/ec38df8985de48ce",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-15/e4b598d99f084c19",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-16/d3fcadde497d307e",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-19/377207ae9cea98d3",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-20/25371c27f3a02a70",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-21/dd391cb2bc59b2c6",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-22/9691b8ad1b066a80",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-27/87dff0f47fd344e7"
                ],
                "ingest_run_ids": [
                    "ingest-06e31af273c7",
                    "ingest-0bd36c20aa94",
                    "ingest-165e0a945a5e",
                    "ingest-207617dd7ceb",
                    "ingest-38c8baa26a52",
                    "ingest-67efeabf184f",
                    "ingest-72b7157bf39f",
                    "ingest-84555a4c38a4",
                    "ingest-be034098f497",
                    "ingest-c416f857e4fa",
                    "ingest-e53a86463995"
                ],
                "source_ids": [
                    "tw-official:tw_price_daily:TWSE:2330:05bca2967dc31336",
                    "tw-official:tw_price_daily:TWSE:2330:0a8ac12f9dd41fe4",
                    "tw-official:tw_price_daily:TWSE:2330:0b4d1f4c49e30fd9",
                    "tw-official:tw_price_daily:TWSE:2330:11d3e2b9da811052",
                    "tw-official:tw_price_daily:TWSE:2330:1d6dab1e84b80fdb",
                    "tw-official:tw_price_daily:TWSE:2330:238532f02e22165f",
                    "tw-official:tw_price_daily:TWSE:2330:25371c27f3a02a70",
                    "tw-official:tw_price_daily:TWSE:2330:34cb2f6fafcb1fb9",
                    "tw-official:tw_price_daily:TWSE:2330:3521c8e4c5014967",
                    "tw-official:tw_price_daily:TWSE:2330:377207ae9cea98d3",
                    "tw-official:tw_price_daily:TWSE:2330:37806df652ed08ad",
                    "tw-official:tw_price_daily:TWSE:2330:3fdfa78a63cc6d7d",
                    "tw-official:tw_price_daily:TWSE:2330:469fa93787137ead",
                    "tw-official:tw_price_daily:TWSE:2330:47104356c7fb192a",
                    "tw-official:tw_price_daily:TWSE:2330:4d265a41afde9033",
                    "tw-official:tw_price_daily:TWSE:2330:523257279b4c61f2",
                    "tw-official:tw_price_daily:TWSE:2330:5257e4b094f61680",
                    "tw-official:tw_price_daily:TWSE:2330:53197c0eb80d9a79",
                    "tw-official:tw_price_daily:TWSE:2330:53fc49a2eabe1a60",
                    "tw-official:tw_price_daily:TWSE:2330:58e5badefcb24b26",
                    "tw-official:tw_price_daily:TWSE:2330:6450635b77534ef3",
                    "tw-official:tw_price_daily:TWSE:2330:6f629e3bdaa4d52b",
                    "tw-official:tw_price_daily:TWSE:2330:7e3e16125d62c8e2",
                    "tw-official:tw_price_daily:TWSE:2330:820530d545bb5c38",
                    "tw-official:tw_price_daily:TWSE:2330:87dff0f47fd344e7",
                    "tw-official:tw_price_daily:TWSE:2330:87f48a6834d99cb2",
                    "tw-official:tw_price_daily:TWSE:2330:8908237b6ac95ed9",
                    "tw-official:tw_price_daily:TWSE:2330:94715490491f5d22",
                    "tw-official:tw_price_daily:TWSE:2330:9691b8ad1b066a80",
                    "tw-official:tw_price_daily:TWSE:2330:9ce71bc7f3123ad9",
                    "tw-official:tw_price_daily:TWSE:2330:a2eb8eb547ae72d5",
                    "tw-official:tw_price_daily:TWSE:2330:a63257be0d676e8a",
                    "tw-official:tw_price_daily:TWSE:2330:ae55e73ad36f33d5",
                    "tw-official:tw_price_daily:TWSE:2330:b7cccaadeb0de836",
                    "tw-official:tw_price_daily:TWSE:2330:baa9a905ee4c2e18",
                    "tw-official:tw_price_daily:TWSE:2330:be1c4fec958d482d",
                    "tw-official:tw_price_daily:TWSE:2330:bf5f15a59e19ad0a",
                    "tw-official:tw_price_daily:TWSE:2330:c15db3967fe11de8",
                    "tw-official:tw_price_daily:TWSE:2330:c8babce8ba646574",
                    "tw-official:tw_price_daily:TWSE:2330:d3fcadde497d307e",
                    "tw-official:tw_price_daily:TWSE:2330:d431e8abd8d642af",
                    "tw-official:tw_price_daily:TWSE:2330:dd391cb2bc59b2c6",
                    "tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19",
                    "tw-official:tw_price_daily:TWSE:2330:e759774fff2be04d",
                    "tw-official:tw_price_daily:TWSE:2330:e961087e0abb112e",
                    "tw-official:tw_price_daily:TWSE:2330:ea38784557eda681",
                    "tw-official:tw_price_daily:TWSE:2330:ec38df8985de48ce",
                    "tw-official:tw_price_daily:TWSE:2330:f68a82442a25756c",
                    "tw-official:tw_price_daily:TWSE:2330:fa96f9c7d494cd4b",
                    "tw-official:tw_price_daily:TWSE:2330:fc286d5a7640de26"
                ]
            },
            "observed_at": "2026-10-27T13:00:38Z",
            "price": 2400.0,
            "source_ref": "source-ingest://snapshots/mss-6c6e5f3d50b58e4304aff864"
        }
    },
    "_positions_by_symbol": {
        "2330.TW": -1.0
    },
    "_valuation_attempt_at": "2026-10-27T19:54:11Z",
    "aggregate_id": "tj_f1ec4785-e662-5af2-9e84-fb7d28d0440b",
    "aggregate_type": "trade_journey",
    "artifact_id": "artifact-persona-paper-d63a91e1b498113c5ddb",
    "artifact_version": "1.0.0",
    "authority_refs": {
        "authority_source": "runtime_binding",
        "persona_id": "persona-34c856860d59d103760c",
        "runtime_mode": "paper",
        "runtime_role": "pantheon-lean-paper-runtime",
        "write_owner": "runtime-manager"
    },
    "avg_slippage_bps": 0.0,
    "avg_slippage_bps_at": "2026-10-27T13:01:55Z",
    "avg_slippage_bps_binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
    "binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
    "broker_status": "not_applicable",
    "capital_pool_id": "pool-persona-paper-d63a91e1b498113c5ddb",
    "causal_parent_id": "76f4a001-22f0-5452-b56b-5dd7d4da3f31",
    "collected_at": "2026-10-27T19:54:11Z",
    "connectivity_status": "connected",
    "correlation_envelope": {
        "causation_event_id": "76f4a001-22f0-5452-b56b-5dd7d4da3f31",
        "correlation_id": "0a9107ea-7bde-57ab-ad24-a63bf4fce802",
        "environment": "paper",
        "event_id": "a07ea9b8-2c97-4506-b4c4-086d26184485",
        "event_time": "2026-10-27T13:02:38Z",
        "journey_id": "tj_f1ec4785-e662-5af2-9e84-fb7d28d0440b",
        "producer": "execution.paper_runtime",
        "producer_revision": 1,
        "received_at": "2026-10-27T13:02:38Z",
        "schema_version": "trade-journey-envelope/1",
        "tenant_id": "tenant-dev",
        "trace_id": "ad4de3d0-cbbe-588e-a6ce-d7eee583e095"
    },
    "deployment_plan_id": "plan-persona-paper-d63a91e1b498113c5ddb",
    "deployment_stage": "paper",
    "drawdown": 0.00015,
    "drawdown_at": "2026-10-27T13:01:35Z",
    "drawdown_binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
    "environment": "paper",
    "event_delivery_lag_ms": 57.151,
    "executed_trade_count": 1,
    "execution_mode": "paper",
    "fill_rate": 0.0,
    "fill_rate_at": "2026-10-27T13:02:38Z",
    "fill_rate_binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
    "health_summary": {
        "bridge": "not_applicable",
        "broker": "not_applicable",
        "paper_runtime": "ok",
        "telemetry": "ok"
    },
    "id": "rt-81e2f12f",
    "journey_id": "tj_f1ec4785-e662-5af2-9e84-fb7d28d0440b",
    "last_event_at": "2026-10-27T19:54:11Z",
    "last_event_id": "cb71a62f-d073-496c-82b4-336cf1b50b6b",
    "last_event_type": "heartbeat",
    "last_fill": {
        "action": "SELL",
        "at": "2026-10-27T01:43:10Z",
        "fill_price": 2385.0,
        "quantity": -1.0,
        "submitted_to_broker": true,
        "symbol": "2330.TW"
    },
    "last_heartbeat_at": "2026-10-27T19:54:11Z",
    "last_heartbeat_event_id": "cb71a62f-d073-496c-82b4-336cf1b50b6b",
    "last_lifecycle_identity": {
        "aggregate_id": "tj_f1ec4785-e662-5af2-9e84-fb7d28d0440b",
        "aggregate_type": "trade_journey",
        "artifact_id": "artifact-persona-paper-d63a91e1b498113c5ddb",
        "artifact_version": "1.0.0",
        "authority_refs": {
            "authority_source": "runtime_binding",
            "persona_id": "persona-34c856860d59d103760c",
            "runtime_mode": "paper",
            "runtime_role": "pantheon-lean-paper-runtime",
            "write_owner": "runtime-manager"
        },
        "binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
        "capital_pool_id": "pool-persona-paper-d63a91e1b498113c5ddb",
        "causal_parent_id": "76f4a001-22f0-5452-b56b-5dd7d4da3f31",
        "correlation_envelope": {
            "causation_event_id": "76f4a001-22f0-5452-b56b-5dd7d4da3f31",
            "correlation_id": "0a9107ea-7bde-57ab-ad24-a63bf4fce802",
            "environment": "paper",
            "event_id": "a07ea9b8-2c97-4506-b4c4-086d26184485",
            "event_time": "2026-10-27T13:02:38Z",
            "journey_id": "tj_f1ec4785-e662-5af2-9e84-fb7d28d0440b",
            "producer": "execution.paper_runtime",
            "producer_revision": 1,
            "received_at": "2026-10-27T13:02:38Z",
            "schema_version": "trade-journey-envelope/1",
            "tenant_id": "tenant-dev",
            "trace_id": "ad4de3d0-cbbe-588e-a6ce-d7eee583e095"
        },
        "created_at": "2026-10-27T13:02:38Z",
        "deployment_stage": "paper",
        "environment": "paper",
        "event_id": "a07ea9b8-2c97-4506-b4c4-086d26184485",
        "event_type": "paper_order_simulated",
        "execution_mode": "paper",
        "journey_id": "tj_f1ec4785-e662-5af2-9e84-fb7d28d0440b",
        "loop_run_id": "lr-run-rb-ebd0dfbd7e10438ebf25d9cc4f6f3292-6686495046b6c023b19ae417",
        "metadata": {
            "binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
            "broker_submission_status": "not_submitted_signal_filtered",
            "capital_scale_pct": 0,
            "causal_parent_id": "76f4a001-22f0-5452-b56b-5dd7d4da3f31",
            "computed_quantity": 0.0,
            "correlation_envelope": {
                "causation_event_id": "d8f11f2f-bd3f-4621-bc29-8f8135ff7b6c",
                "correlation_id": "0a9107ea-7bde-57ab-ad24-a63bf4fce802",
                "environment": "paper",
                "event_id": "76f4a001-22f0-5452-b56b-5dd7d4da3f31",
                "event_time": "2026-10-27T13:01:32Z",
                "journey_id": "tj_f1ec4785-e662-5af2-9e84-fb7d28d0440b",
                "producer": "execution.paper_runtime",
                "producer_revision": 1,
                "received_at": "2026-10-27T13:01:32Z",
                "schema_version": "trade-journey-envelope/1",
                "tenant_id": "tenant-dev",
                "trace_id": "ad4de3d0-cbbe-588e-a6ce-d7eee583e095"
            },
            "decision_status": "no_order",
            "environment": "paper",
            "execution_fence_status": "in_progress",
            "filter_reason": "execution_in_progress_elsewhere",
            "is_real_capital": false,
            "is_real_order": false,
            "journey_id": "tj_f1ec4785-e662-5af2-9e84-fb7d28d0440b",
            "loop_run_id": "lr-run-rb-ebd0dfbd7e10438ebf25d9cc4f6f3292-6686495046b6c023b19ae417",
            "market_price": 2400.0,
            "market_price_as_of": "2026-10-27T00:00:00Z",
            "market_price_source": "source-ingest://snapshots/mss-6c6e5f3d50b58e4304aff864",
            "noop_reason": "execution_in_progress_elsewhere",
            "order_status": "not_submitted",
            "order_type": "MARKET",
            "persona_id": "persona-34c856860d59d103760c",
            "price": 2400.0,
            "quantity_type": "SHARES",
            "requested_quantity": 1.0,
            "run_id": "run-rb-ebd0dfbd7e10438ebf25d9cc4f6f3292-6686495046b6c023b19ae417",
            "runtime_binding_effective_at": "2026-10-27T01:42:12Z",
            "runtime_package": "paper_execution_runtime",
            "runtime_role": "pantheon-lean-paper-runtime",
            "sequence_no": 6,
            "signal_id": "sig-6686495046b6c023b19ae417e55399f8",
            "sim_fill_flag": false,
            "source_mode": "live",
            "source_worker": "paper-signal-producer",
            "strategy_id": "strategy-persona-d63a91e1b498113c5ddb",
            "submitted_to_broker": false,
            "symbol": "2330.TW",
            "tenant_id": "tenant-dev"
        },
        "persona_capital_binding_id": "pcb-persona-paper-d63a91e1b498113c5ddb",
        "plan_id": "plan-persona-paper-d63a91e1b498113c5ddb",
        "run_id": "run-rb-ebd0dfbd7e10438ebf25d9cc4f6f3292-6686495046b6c023b19ae417",
        "runtime_id": "rt-81e2f12f",
        "sequence_no": 6,
        "signal_id": "sig-6686495046b6c023b19ae417e55399f8",
        "source_mode": "live",
        "target": {
            "artifact_type": "execution_bundle",
            "artifact_version": "1.0.0",
            "promotion_state": "paper",
            "registry_id": "artifact-persona-paper-d63a91e1b498113c5ddb",
            "strategy_id": "strategy-persona-d63a91e1b498113c5ddb"
        },
        "tenant_id": "tenant-dev",
        "trace_id": "ad4de3d0-cbbe-588e-a6ce-d7eee583e095"
    },
    "loop_run_id": "lr-run-rb-ebd0dfbd7e10438ebf25d9cc4f6f3292-6686495046b6c023b19ae417",
    "persona_capital_binding_id": "pcb-persona-paper-d63a91e1b498113c5ddb",
    "plan_id": "plan-persona-paper-d63a91e1b498113c5ddb",
    "pnl": -15.0,
    "pnl_at": "2026-10-27T13:01:35Z",
    "pnl_binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
    "position_count": 1,
    "positions": [
        {
            "mark_as_of": "2026-10-27T00:00:00Z",
            "mark_calendar_evidence": {
                "authority": "Taiwan Stock Exchange 115 年10月23日休市公告 (2026-10-22)",
                "checksum": "48e8d89449b436d936cb4a25c9860141b11a838d4f873deb2717af9fd896df99",
                "coverage_end": "2026-10-28",
                "coverage_start": "2026-10-22",
                "fetched_at": "2026-10-23T07:00:39Z",
                "holidays": {
                    "2026-10-23": {
                        "name": "颱風全日休市 (TWSE 臨時休市公告)"
                    },
                    "2026-10-25": {
                        "name": "臺灣光復暨金門古寧頭大捷紀念日"
                    },
                    "2026-10-26": {
                        "name": "臺灣光復暨金門古寧頭大捷紀念日"
                    }
                },
                "market": "TW",
                "source_url": "https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6a0fa351b01a1638507940202.pdf",
                "timezone": "Asia/Taipei",
                "trading_days": [
                    "2026-10-22",
                    "2026-10-27",
                    "2026-10-28"
                ],
                "venue": "TWSE",
                "version": "twse-2026-emergency-closure-20261023-v2"
            },
            "mark_lineage": {
                "connector_ids": [
                    "tw-twse-tpex-official-market"
                ],
                "content_refs": [
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-12/b7cccaadeb0de836",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-13/fa96f9c7d494cd4b",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-14/3fdfa78a63cc6d7d",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-17/be1c4fec958d482d",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-18/6f629e3bdaa4d52b",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-19/5257e4b094f61680",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-20/4d265a41afde9033",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-21/c15db3967fe11de8",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-24/3521c8e4c5014967",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-25/ae55e73ad36f33d5",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-26/34cb2f6fafcb1fb9",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-27/58e5badefcb24b26",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-28/8908237b6ac95ed9",
                    "tw-official://tw_price_daily/TWSE/2330/2026-08-31/9ce71bc7f3123ad9",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-01/6450635b77534ef3",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-02/7e3e16125d62c8e2",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-03/11d3e2b9da811052",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-04/c8babce8ba646574",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-07/469fa93787137ead",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-08/820530d545bb5c38",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-09/ea38784557eda681",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-10/a63257be0d676e8a",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-11/1d6dab1e84b80fdb",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-14/f68a82442a25756c",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-15/0b4d1f4c49e30fd9",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-16/0a8ac12f9dd41fe4",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-17/37806df652ed08ad",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-18/bf5f15a59e19ad0a",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-21/fc286d5a7640de26",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-22/e759774fff2be04d",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-23/238532f02e22165f",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-24/05bca2967dc31336",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-29/523257279b4c61f2",
                    "tw-official://tw_price_daily/TWSE/2330/2026-09-30/47104356c7fb192a",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-01/53fc49a2eabe1a60",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-02/53197c0eb80d9a79",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-05/94715490491f5d22",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-06/e961087e0abb112e",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-07/baa9a905ee4c2e18",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-08/d431e8abd8d642af",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-12/87f48a6834d99cb2",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-13/a2eb8eb547ae72d5",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-14/ec38df8985de48ce",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-15/e4b598d99f084c19",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-16/d3fcadde497d307e",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-19/377207ae9cea98d3",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-20/25371c27f3a02a70",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-21/dd391cb2bc59b2c6",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-22/9691b8ad1b066a80",
                    "tw-official://tw_price_daily/TWSE/2330/2026-10-27/87dff0f47fd344e7"
                ],
                "ingest_run_ids": [
                    "ingest-06e31af273c7",
                    "ingest-0bd36c20aa94",
                    "ingest-165e0a945a5e",
                    "ingest-207617dd7ceb",
                    "ingest-38c8baa26a52",
                    "ingest-67efeabf184f",
                    "ingest-72b7157bf39f",
                    "ingest-84555a4c38a4",
                    "ingest-be034098f497",
                    "ingest-c416f857e4fa",
                    "ingest-e53a86463995"
                ],
                "source_ids": [
                    "tw-official:tw_price_daily:TWSE:2330:05bca2967dc31336",
                    "tw-official:tw_price_daily:TWSE:2330:0a8ac12f9dd41fe4",
                    "tw-official:tw_price_daily:TWSE:2330:0b4d1f4c49e30fd9",
                    "tw-official:tw_price_daily:TWSE:2330:11d3e2b9da811052",
                    "tw-official:tw_price_daily:TWSE:2330:1d6dab1e84b80fdb",
                    "tw-official:tw_price_daily:TWSE:2330:238532f02e22165f",
                    "tw-official:tw_price_daily:TWSE:2330:25371c27f3a02a70",
                    "tw-official:tw_price_daily:TWSE:2330:34cb2f6fafcb1fb9",
                    "tw-official:tw_price_daily:TWSE:2330:3521c8e4c5014967",
                    "tw-official:tw_price_daily:TWSE:2330:377207ae9cea98d3",
                    "tw-official:tw_price_daily:TWSE:2330:37806df652ed08ad",
                    "tw-official:tw_price_daily:TWSE:2330:3fdfa78a63cc6d7d",
                    "tw-official:tw_price_daily:TWSE:2330:469fa93787137ead",
                    "tw-official:tw_price_daily:TWSE:2330:47104356c7fb192a",
                    "tw-official:tw_price_daily:TWSE:2330:4d265a41afde9033",
                    "tw-official:tw_price_daily:TWSE:2330:523257279b4c61f2",
                    "tw-official:tw_price_daily:TWSE:2330:5257e4b094f61680",
                    "tw-official:tw_price_daily:TWSE:2330:53197c0eb80d9a79",
                    "tw-official:tw_price_daily:TWSE:2330:53fc49a2eabe1a60",
                    "tw-official:tw_price_daily:TWSE:2330:58e5badefcb24b26",
                    "tw-official:tw_price_daily:TWSE:2330:6450635b77534ef3",
                    "tw-official:tw_price_daily:TWSE:2330:6f629e3bdaa4d52b",
                    "tw-official:tw_price_daily:TWSE:2330:7e3e16125d62c8e2",
                    "tw-official:tw_price_daily:TWSE:2330:820530d545bb5c38",
                    "tw-official:tw_price_daily:TWSE:2330:87dff0f47fd344e7",
                    "tw-official:tw_price_daily:TWSE:2330:87f48a6834d99cb2",
                    "tw-official:tw_price_daily:TWSE:2330:8908237b6ac95ed9",
                    "tw-official:tw_price_daily:TWSE:2330:94715490491f5d22",
                    "tw-official:tw_price_daily:TWSE:2330:9691b8ad1b066a80",
                    "tw-official:tw_price_daily:TWSE:2330:9ce71bc7f3123ad9",
                    "tw-official:tw_price_daily:TWSE:2330:a2eb8eb547ae72d5",
                    "tw-official:tw_price_daily:TWSE:2330:a63257be0d676e8a",
                    "tw-official:tw_price_daily:TWSE:2330:ae55e73ad36f33d5",
                    "tw-official:tw_price_daily:TWSE:2330:b7cccaadeb0de836",
                    "tw-official:tw_price_daily:TWSE:2330:baa9a905ee4c2e18",
                    "tw-official:tw_price_daily:TWSE:2330:be1c4fec958d482d",
                    "tw-official:tw_price_daily:TWSE:2330:bf5f15a59e19ad0a",
                    "tw-official:tw_price_daily:TWSE:2330:c15db3967fe11de8",
                    "tw-official:tw_price_daily:TWSE:2330:c8babce8ba646574",
                    "tw-official:tw_price_daily:TWSE:2330:d3fcadde497d307e",
                    "tw-official:tw_price_daily:TWSE:2330:d431e8abd8d642af",
                    "tw-official:tw_price_daily:TWSE:2330:dd391cb2bc59b2c6",
                    "tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19",
                    "tw-official:tw_price_daily:TWSE:2330:e759774fff2be04d",
                    "tw-official:tw_price_daily:TWSE:2330:e961087e0abb112e",
                    "tw-official:tw_price_daily:TWSE:2330:ea38784557eda681",
                    "tw-official:tw_price_daily:TWSE:2330:ec38df8985de48ce",
                    "tw-official:tw_price_daily:TWSE:2330:f68a82442a25756c",
                    "tw-official:tw_price_daily:TWSE:2330:fa96f9c7d494cd4b",
                    "tw-official:tw_price_daily:TWSE:2330:fc286d5a7640de26"
                ]
            },
            "mark_observed_at": "2026-10-27T13:00:38Z",
            "mark_price": 2400.0,
            "mark_source_ref": "source-ingest://snapshots/mss-6c6e5f3d50b58e4304aff864",
            "market_value": -2400.0,
            "quantity": -1.0,
            "symbol": "2330.TW"
        }
    ],
    "projection_source": "telemetry_ingest",
    "projection_updated_at": "2026-10-27T19:54:11Z",
    "queue_lag_ms": 0.0,
    "recent_lifecycle_event_ids": [
        "4666c4c2-0bbc-463b-b73f-f94cac7c197e",
        "0bf024af-d51c-56c3-95f0-ecb67cde9748",
        "66ac5547-cbd3-54fe-a41c-af98aa6f046d",
        "ccd4db00-0440-568e-9ea0-09949d19ad26",
        "e5ab25f4-ff48-525f-95b9-4c347f8b4024",
        "e57ab8d8-bd2a-419c-aebb-db0c3ddd9f61",
        "8da58fb2-929b-5582-87fc-53b37dda588b",
        "31b7d021-33cf-54bd-94a3-681009bed0a7",
        "6cd4dd86-4435-45de-88df-460c713718f6",
        "05d58469-0438-5010-8604-a3ea42dbefa4",
        "3b035c9f-43f2-5f6e-beb7-e99f178489ed",
        "d8f11f2f-bd3f-4621-bc29-8f8135ff7b6c",
        "76f4a001-22f0-5452-b56b-5dd7d4da3f31",
        "a07ea9b8-2c97-4506-b4c4-086d26184485"
    ],
    "run_id": "run-rb-ebd0dfbd7e10438ebf25d9cc4f6f3292-6686495046b6c023b19ae417",
    "runtime_binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
    "runtime_id": "rt-81e2f12f",
    "runtime_package": "paper_execution_runtime",
    "sequence_no": 6,
    "signal_id": "sig-6686495046b6c023b19ae417e55399f8",
    "source_mode": "live",
    "state": "active",
    "tenant_id": "tenant-dev",
    "total_trades": 1,
    "trace_id": "ad4de3d0-cbbe-588e-a6ce-d7eee583e095",
    "window": "latest"
}
""")

_REAL_D551_HEARTBEAT = json.loads(r"""
{
    "artifact_id": "artifact-persona-paper-d63a91e1b498113c5ddb",
    "artifact_version": "1.0.0",
    "authority_refs": {
        "authority_source": "runtime_binding",
        "persona_id": "persona-34c856860d59d103760c",
        "runtime_mode": "paper",
        "runtime_role": "pantheon-lean-paper-runtime",
        "write_owner": "runtime-manager"
    },
    "binding_id": "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292",
    "capital_pool_id": "pool-persona-paper-d63a91e1b498113c5ddb",
    "created_at": "2026-10-27T19:54:11Z",
    "deployment_stage": "paper",
    "environment": "paper",
    "event_id": "cb71a62f-d073-496c-82b4-336cf1b50b6b",
    "event_type": "heartbeat",
    "execution_mode": "paper",
    "metadata": {
        "capital_scale_pct": 0,
        "delivery_lag_ms": 57.151,
        "event_delivery_lag_ms": 57.151,
        "is_real_capital": false,
        "is_real_order": false,
        "performance_telemetry": {
            "as_of": "2026-10-27T13:01:35Z",
            "attempted_at": "2026-10-27T19:54:11Z",
            "code": "performance_sample_unchanged",
            "fill_count": 2,
            "mark_count": 0,
            "mark_source": {
                "enabled": true,
                "indexed_mark_count": 0,
                "last_error": null,
                "last_refresh_at": null,
                "max_mark_age_seconds": 172800.0,
                "requested_symbols": [],
                "source": "source_ingest",
                "url": "http://source-ingest:8097"
            },
            "position_count": 0,
            "status": "unchanged",
            "valuation_method": "fill_cash_ledger_mark_to_market"
        },
        "persona_id": "persona-34c856860d59d103760c",
        "queue_depth": 0,
        "queue_lag_ms": 0.0,
        "runtime_binding_effective_at": "2026-10-27T01:42:12Z",
        "runtime_heartbeat": {
            "broker_status": "not_applicable",
            "connectivity_status": "connected",
            "delivery_lag_ms": 57.151,
            "event_delivery_lag_ms": 57.151,
            "queue_lag_ms": 0.0
        },
        "runtime_package": "paper_execution_runtime",
        "runtime_role": "pantheon-lean-paper-runtime",
        "sim_fill_flag": false
    },
    "metrics": {
        "delivery_lag_ms": 57.151,
        "event_delivery_lag_ms": 57.151,
        "heartbeat": 1,
        "queue_lag_ms": 0.0
    },
    "persona_capital_binding_id": "pcb-persona-paper-d63a91e1b498113c5ddb",
    "plan_id": "plan-persona-paper-d63a91e1b498113c5ddb",
    "runtime_id": "rt-81e2f12f",
    "target": {
        "artifact_type": "execution_bundle",
        "artifact_version": "1.0.0",
        "promotion_state": "paper",
        "registry_id": "artifact-persona-paper-d63a91e1b498113c5ddb",
        "strategy_id": "strategy-persona-d63a91e1b498113c5ddb"
    },
    "tenant_id": "tenant-dev"
}
""")


class TestRealStateColdStartUnchangedHeartbeat(unittest.TestCase):
    """Silent d551 defect: no exception; stale short 1 survives a flat committed ledger.

    The interrupting trigger at 13:01 is NOT established by this replay; it only
    covers the observable projection failure on the original saved inputs.
    """

    def _store_from_saved_summary(self):
        path = Path(tempfile.mkdtemp()) / "summaries.json"
        record = json.loads(json.dumps(_REAL_D551_SUMMARY))
        path.write_text(json.dumps({record["id"]: record}), encoding="utf-8")
        return RuntimeSummaryProjectionStore(path=str(path))

    def test_saved_summary_is_the_stale_short_state(self):
        self.assertEqual(_REAL_D551_SUMMARY["position_count"], 1)
        self.assertEqual(_REAL_D551_SUMMARY["positions"][0]["quantity"], -1.0)
        self.assertEqual(_REAL_D551_SUMMARY["positions"][0]["market_value"], -2400.0)
        self.assertEqual(_REAL_D551_SUMMARY["executed_trade_count"], 1)

    def test_original_unchanged_heartbeat_withholds_stale_short(self):
        store = self._store_from_saved_summary()
        telemetry = _REAL_D551_HEARTBEAT["metadata"]["performance_telemetry"]
        self.assertEqual(telemetry["status"], "unchanged")
        self.assertEqual(telemetry["fill_count"], 2)
        self.assertEqual(telemetry["position_count"], 0)
        summary = store.project_event(json.loads(json.dumps(_REAL_D551_HEARTBEAT)))
        self.assertEqual(summary["binding_id"], "rb-ebd0dfbd7e10438ebf25d9cc4f6f3292")
        self.assertEqual(summary["ledger_fill_count"], 2)
        self.assertEqual(summary["executed_trade_count"], 1)
        self.assertEqual(summary["positions"], [])
        self.assertIsNone(summary["position_count"])
        self.assertEqual(summary["positions_status"], "unavailable")
        self.assertIn("fill_event_gap", summary["positions_unavailable_reason"])
        # Real financials and their original as-of are preserved, not refreshed.
        self.assertEqual(summary["pnl"], -15.0)
        self.assertEqual(summary["drawdown"], 0.00015)
        self.assertEqual(summary["pnl_at"], "2026-10-27T13:01:35Z")
        self.assertEqual(summary["drawdown_at"], "2026-10-27T13:01:35Z")
        # No fabricated fill event.
        self.assertEqual(summary["last_fill"]["action"], "SELL")

    def test_paper_emitter_metric_event_ids_preserve_causal_event_across_echo_out_of_order_and_rollover(self):
        from services.execution.lean_runtime.paper_runtime import (
            RuntimeIdentity,
            RuntimeTelemetryEmitter,
        )

        class _Resolver:
            def __init__(self, binding_id: str, effective_at: str) -> None:
                self.binding_id = binding_id
                self.effective_at = effective_at

            def resolve(self):
                return {
                    "binding_id": self.binding_id,
                    "runtime_id": "rt-causal-metric-001",
                    "capital_pool_id": "pool-causal-001",
                    "artifact_id": "artifact-causal-001",
                    "artifact_version": "1.0.0",
                    "plan_id": "plan-causal-001",
                    "persona_capital_binding_id": "pcb-causal-001",
                    "deployment_stage": "paper",
                    "deployment_mode": "paper",
                    "effective_at": self.effective_at,
                }

        identity = RuntimeIdentity.from_env(
            {
                "PANTHEON_RUNTIME_ROLE": "paper",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_RUNTIME_ID": "rt-causal-metric-001",
                "PANTHEON_WORKSPACE_REF": "workspace-causal",
                "PANTHEON_AUTH_PROFILE_REF": "auth-causal",
                "PANTHEON_PERSONA_ID": "persona-causal",
                "PANTHEON_SESSION_ID": "session-causal",
                "PANTHEON_TRACE_ID": "trace-causal-001",
                "PANTHEON_REQUEST_ID": "request-causal-001",
            }
        )
        resolver = _Resolver("rb-causal-001", "2026-11-02T00:00:00Z")
        emitter = RuntimeTelemetryEmitter(identity, resolver)
        store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)

        hb1 = emitter.build_event(
            "heartbeat",
            {"queue_lag_ms": 2.0, "delivery_lag_ms": 4.0},
            created_at="2026-11-02T00:00:01Z",
        )
        summary = store.project_event(hb1)
        self.assertNotIn("drawdown_event_id", summary)
        self.assertNotIn("pnl_event_id", summary)

        pnl_ev = emitter.build_event(
            "pnl_snapshot",
            {"pnl": -55.0, "pnl_as_of": "2026-11-02T00:00:10Z"},
            created_at="2026-11-02T00:00:10Z",
        )
        dd_ev = emitter.build_event(
            "drawdown_snapshot",
            {"drawdown": 0.04, "drawdown_as_of": "2026-11-02T00:00:12Z"},
            created_at="2026-11-02T00:00:12Z",
        )
        store.project_event(pnl_ev)
        summary = store.project_event(dd_ev)
        self.assertEqual(summary["pnl_event_id"], pnl_ev["event_id"])
        self.assertEqual(summary["drawdown_event_id"], dd_ev["event_id"])

        # Out-of-order event with older as-of never moves drawdown_event_id or pnl_event_id
        older_dd = emitter.build_event(
            "drawdown_snapshot",
            {"drawdown": 0.08, "drawdown_as_of": "2026-11-02T00:00:05Z"},
            created_at="2026-11-02T00:00:20Z",
        )
        older_pnl = emitter.build_event(
            "pnl_snapshot",
            {"pnl": -120.0, "pnl_as_of": "2026-11-02T00:00:05Z"},
            created_at="2026-11-02T00:00:21Z",
        )
        store.project_event(older_dd)
        summary = store.project_event(older_pnl)
        self.assertEqual(summary["drawdown_event_id"], dd_ev["event_id"])
        self.assertEqual(summary["pnl_event_id"], pnl_ev["event_id"])

        # Threshold-derived echo event never moves drawdown_event_id or pnl_event_id
        echo_dd = emitter.build_event(
            "drawdown_snapshot",
            {"drawdown": 0.09, "drawdown_as_of": "2026-11-02T00:00:25Z"},
            metadata={"derived_from_threshold_evaluation": True},
            created_at="2026-11-02T00:00:25Z",
        )
        echo_pnl = emitter.build_event(
            "pnl_snapshot",
            {"pnl": -500.0, "pnl_as_of": "2026-11-02T00:00:26Z"},
            metadata={"derived_from_threshold_evaluation": True},
            created_at="2026-11-02T00:00:26Z",
        )
        store.project_event(echo_dd)
        summary = store.project_event(echo_pnl)
        self.assertEqual(summary["drawdown_event_id"], dd_ev["event_id"])
        self.assertEqual(summary["pnl_event_id"], pnl_ev["event_id"])

        # Event that updates a metric but carries no event_id drops the stale {metric}_event_id
        # instead of keeping the old one, so the value and its event never disagree.
        no_id_dd = dict(
            emitter.build_event(
                "drawdown_snapshot",
                {"drawdown": 0.042, "drawdown_as_of": "2026-11-02T00:00:30Z"},
                created_at="2026-11-02T00:00:30Z",
            )
        )
        no_id_dd.pop("event_id", None)
        no_id_pnl = dict(
            emitter.build_event(
                "pnl_snapshot",
                {"pnl": -60.0, "pnl_as_of": "2026-11-02T00:00:31Z"},
                created_at="2026-11-02T00:00:31Z",
            )
        )
        no_id_pnl["event_id"] = "   "
        store.project_event(no_id_dd)
        summary = store.project_event(no_id_pnl)
        self.assertEqual(summary["drawdown"], 0.042)
        self.assertEqual(summary["pnl"], -60.0)
        self.assertNotIn("drawdown_event_id", summary)
        self.assertNotIn("pnl_event_id", summary)

        # Re-establish event_ids before testing binding rollover
        dd_ev_2 = emitter.build_event(
            "drawdown_snapshot",
            {"drawdown": 0.043, "drawdown_as_of": "2026-11-02T00:00:35Z"},
            created_at="2026-11-02T00:00:35Z",
        )
        pnl_ev_2 = emitter.build_event(
            "pnl_snapshot",
            {"pnl": -62.0, "pnl_as_of": "2026-11-02T00:00:36Z"},
            created_at="2026-11-02T00:00:36Z",
        )
        store.project_event(dd_ev_2)
        summary = store.project_event(pnl_ev_2)
        self.assertEqual(summary["drawdown_event_id"], dd_ev_2["event_id"])
        self.assertEqual(summary["pnl_event_id"], pnl_ev_2["event_id"])

        # Newer binding rollover clears drawdown_event_id and pnl_event_id
        resolver.binding_id = "rb-causal-002"
        resolver.effective_at = "2026-11-02T00:01:00Z"
        hb2 = emitter.build_event(
            "heartbeat",
            {"queue_lag_ms": 1.0, "delivery_lag_ms": 2.0},
            created_at="2026-11-02T00:01:01Z",
        )
        summary = store.project_event(hb2)
        self.assertEqual(summary["binding_id"], "rb-causal-002")
        self.assertNotIn("drawdown_event_id", summary)
        self.assertNotIn("pnl_event_id", summary)


class TestPositionCostCurrencyBrokerProjection(unittest.TestCase):
    """Tests for TELEMETRY-POSITION-COST-CURRENCY-20261106 (AC 1 - AC 7)."""

    @staticmethod
    def _ledger_fill(
        *,
        runtime_id: str = "rt-cost-001",
        binding_id: str = "rb-cost-001",
        tenant_id: str = "tenant-dev",
        symbol: str = "2330.TWSE",
        quantity: float = -1.0,
        price: Any = 2370.0,
        ledger_fill_count: Any = 1,
        currency: Optional[str] = "TWD",
        broker: Optional[str] = "tw-paper-sim",
        created_at: str = "2026-11-05T01:00:00Z",
        event_id: Optional[str] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {"symbol": symbol}
        if ledger_fill_count is not None:
            meta["ledger_fill_count"] = ledger_fill_count
        if currency is not None:
            meta["currency"] = currency
        if broker is not None:
            meta["broker"] = broker
        if extra_metadata:
            meta.update(extra_metadata)
        return {
            "event_id": event_id or f"evt-fill-{runtime_id}-{binding_id}-{ledger_fill_count}",
            "event_type": "paper_fill_simulated",
            "created_at": created_at,
            "tenant_id": tenant_id,
            "environment": "paper",
            "deployment_stage": "paper",
            "runtime_id": runtime_id,
            "binding_id": binding_id,
            "capital_pool_id": "pool-tw-001",
            "artifact_id": "artifact-tw-001",
            "artifact_version": "1.0.0",
            "plan_id": "plan-tw-001",
            "persona_capital_binding_id": "pcb-tw-001",
            "metrics": {
                "fill_quantity": quantity,
                "fill_price": price,
                "action": "BUY" if quantity > 0 else "SELL",
                "submitted_to_broker": True,
            },
            "metadata": meta,
        }

    @staticmethod
    def _mark_snapshot(
        *,
        runtime_id: str = "rt-cost-001",
        binding_id: str = "rb-cost-001",
        tenant_id: str = "tenant-dev",
        symbol: str = "2330.TWSE",
        price: float = 2240.0,
        as_of: str = "2026-11-04T00:00:00Z",
        created_at: str = "2026-11-05T02:00:00Z",
        pnl: float = 0.0,
        fill_count: Optional[int] = None,
        quote_currency: Optional[str] = None,
        source_ref: str = "source-ingest://snapshots/mss-cost-001",
    ) -> dict[str, Any]:
        base_symbol = str(symbol or "").split(".", 1)[0]
        mark_ref: dict[str, Any] = {
            "symbol": symbol,
            "price": price,
            "as_of": as_of,
            "source_ref": source_ref,
        }
        if str(symbol or "").upper().endswith((".TW", ".TWO", ".TWSE", ".TPEX")):
            mark_ref["observed_at"] = _default_tw_observed_at(as_of)
            mark_ref["lineage"] = {
                "source_ids": [f"tw-official:tw_price_daily:TWSE:{base_symbol}:test"],
                "connector_ids": ["tw-twse-tpex-official-market"],
            }
        if quote_currency is not None:
            mark_ref["quote_currency"] = quote_currency
        metrics: dict[str, Any] = {"pnl": pnl}
        if fill_count is not None:
            metrics["fill_count"] = fill_count
        return {
            "event_id": f"evt-pnl-{runtime_id}-{created_at}",
            "event_type": "pnl_snapshot",
            "created_at": created_at,
            "pnl_as_of": as_of,
            "tenant_id": tenant_id,
            "environment": "paper",
            "deployment_stage": "paper",
            "runtime_id": runtime_id,
            "binding_id": binding_id,
            "capital_pool_id": "pool-tw-001",
            "artifact_id": "artifact-tw-001",
            "artifact_version": "1.0.0",
            "plan_id": "plan-tw-001",
            "persona_capital_binding_id": "pcb-tw-001",
            "metrics": metrics,
            "metadata": {"mark_refs": [mark_ref]},
        }

    def test_moving_average_cost_fold_long_short_reduce_flat_and_flip(self) -> None:
        import math

        store = RuntimeSummaryProjectionStore()
        # 1. Open long 2 @ 100, add 2 @ 110 -> avg 105.0, realized 0.0
        store.project_event(
            self._ledger_fill(symbol="AAPL.US", quantity=2.0, price=100.0, ledger_fill_count=1, currency="USD", broker="us-paper-sim")
        )
        summary = store.project_event(
            self._ledger_fill(symbol="AAPL.US", quantity=2.0, price=110.0, ledger_fill_count=2, currency="USD", broker="us-paper-sim")
        )
        row = summary["positions"][0]
        self.assertEqual(row["quantity"], 4.0)
        self.assertEqual(row["average_price"], 105.0)
        self.assertEqual(row["realized_pnl"], 0.0)
        self.assertEqual(math.copysign(1.0, row["realized_pnl"]), 1.0)
        self.assertNotIn("unrealized_pnl", row)

        # 2. Partial reduce: sell -1 @ 115 -> closes 1 * (115 - 105) * (+1) = +10.0, keeps avg 105.0
        summary = store.project_event(
            self._ledger_fill(symbol="AAPL.US", quantity=-1.0, price=115.0, ledger_fill_count=3, currency="USD", broker="us-paper-sim")
        )
        row = summary["positions"][0]
        self.assertEqual(row["quantity"], 3.0)
        self.assertEqual(row["average_price"], 105.0)
        self.assertEqual(row["realized_pnl"], 10.0)

        # 3. Fill to flat: sell -3 @ 101 -> closes 3 * (101 - 105) * (+1) = -12.0 -> cumulative realized -2.0, avg None
        summary = store.project_event(
            self._ledger_fill(symbol="AAPL.US", quantity=-3.0, price=101.0, ledger_fill_count=4, currency="USD", broker="us-paper-sim")
        )
        self.assertEqual(summary["positions"], [])
        self.assertIsNone(summary["_cost_by_symbol"]["AAPL.US"]["average_price"])
        self.assertEqual(summary["_cost_by_symbol"]["AAPL.US"]["realized_pnl"], -2.0)

        # 4. Reopen short -2 @ 120 -> avg 120.0, cumulative realized stays -2.0
        summary = store.project_event(
            self._ledger_fill(symbol="AAPL.US", quantity=-2.0, price=120.0, ledger_fill_count=5, currency="USD", broker="us-paper-sim")
        )
        row = summary["positions"][0]
        self.assertEqual(row["quantity"], -2.0)
        self.assertEqual(row["average_price"], 120.0)
        self.assertEqual(row["realized_pnl"], -2.0)

        # 5. Flip from short -2 @ 120 to long +3 via buy +5 @ 114:
        # closes 2 * (114 - 120) * (-1) = +12.0 -> realized = -2.0 + 12.0 = +10.0; opens +3 @ 114.0
        summary = store.project_event(
            self._ledger_fill(symbol="AAPL.US", quantity=5.0, price=114.0, ledger_fill_count=6, currency="USD", broker="us-paper-sim")
        )
        row = summary["positions"][0]
        self.assertEqual(row["quantity"], 3.0)
        self.assertEqual(row["average_price"], 114.0)
        self.assertEqual(row["realized_pnl"], 10.0)

        # 6. Accepted non-stale mark at 114.0 -> unrealized_pnl is +0.0 (never -0.0)
        summary = store.project_event(
            self._mark_snapshot(symbol="AAPL.US", price=114.0, fill_count=6)
        )
        row = summary["positions"][0]
        self.assertEqual(row["unrealized_pnl"], 0.0)
        self.assertEqual(math.copysign(1.0, row["unrealized_pnl"]), 1.0)

        # 7. Stale mark keeps average_price and realized_pnl and drops unrealized_pnl
        stale_view = store.get(
            "rt-cost-001",
            tenant_id="tenant-dev",
            now=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc),
        )
        stale_row = stale_view["positions"][0]
        self.assertTrue(stale_row["mark_stale"])
        self.assertEqual(stale_row["average_price"], 114.0)
        self.assertEqual(stale_row["realized_pnl"], 10.0)
        self.assertEqual(stale_row["currency"], "USD")
        self.assertEqual(stale_row["broker_id"], "us-paper-sim")
        self.assertNotIn("unrealized_pnl", stale_row)

        # 8. Failed valuation attempt keeps average_price and realized_pnl and drops unrealized_pnl
        failed_summary = store.project_event(
            {
                "event_id": "evt-hb-fail-001",
                "event_type": "heartbeat",
                "created_at": "2026-11-05T05:32:00Z",
                "tenant_id": "tenant-dev",
                "environment": "paper",
                "deployment_stage": "paper",
                "runtime_id": "rt-cost-001",
                "binding_id": "rb-cost-001",
                "metrics": {"heartbeat": 1},
                "metadata": {
                    "performance_telemetry": {
                        "status": "marks_unavailable",
                        "code": "stale_or_future_market_marks",
                        "attempted_at": "2026-11-05T05:32:00Z",
                        "missing_symbols": ["AAPL.US"],
                    }
                },
            }
        )
        failed_row = failed_summary["positions"][0]
        self.assertEqual(failed_row["mark_unavailable_reason"], "stale_or_future_market_marks")
        self.assertEqual(failed_row["average_price"], 114.0)
        self.assertEqual(failed_row["realized_pnl"], 10.0)
        self.assertEqual(failed_row["currency"], "USD")
        self.assertEqual(failed_row["broker_id"], "us-paper-sim")
        self.assertNotIn("unrealized_pnl", failed_row)

    def test_four_real_hosted_fill_sequences_match_hosted_pnl(self) -> None:
        import math

        cases = [
            {
                "runtime_id": "rt-535a82a3",
                "binding_id": "rb-a1c3ada3d5894d57bc2e012f337753be",
                "symbol": "2330.TWSE",
                "fills": [
                    (1, -1.0, 2370.0),
                    (2, -1.0, 2330.0),
                    (3, -1.0, 2295.0),
                    (4, -1.0, 2260.0),
                    (5, -1.0, 2240.0),
                ],
                "mark": 2240.0,
                "hosted_pnl": 295.0,
                "expected_qty": -5.0,
                "expected_avg": 2299.0,
                "expected_unrealized": 295.0,
                "expected_realized": 0.0,
            },
            {
                "runtime_id": "rt-8b0d8649",
                "binding_id": "rb-fd733d7c192f45898bbe0e90c8555878",
                "symbol": "0050.TWSE",
                "fills": [
                    (1, -1.0, 104.95),
                    (2, -1.0, 104.1),
                    (3, -1.0, 100.6),
                    (4, -1.0, 99.85),
                    (5, -1.0, 97.15),
                ],
                "mark": 97.15,
                "hosted_pnl": 20.90000000000873,
                "expected_qty": -5.0,
                "expected_avg": 101.33,
                "expected_unrealized": 20.9,
                "expected_realized": 0.0,
            },
            {
                "runtime_id": "rt-c98e4223",
                "binding_id": "rb-eda9b9a3379d4fb5a09f0e8432c84f20",
                "symbol": "2317.TWSE",
                "fills": [
                    (1, -1.0, 245.5),
                    (2, 1.0, 251.0),
                    (3, -1.0, 245.5),
                    (4, -1.0, 240.0),
                    (5, -1.0, 235.0),
                ],
                "mark": 235.0,
                "hosted_pnl": 10.0,
                "expected_qty": -3.0,
                "expected_avg": 240.166667,
                "expected_unrealized": 15.5,
                "expected_realized": -5.5,
            },
            {
                "runtime_id": "rt-342d76ff",
                "binding_id": "rb-73cafebb37064f039751a7a64d0cf04c",
                "symbol": "2330.TW",
                "fills": [
                    (1, -1.0, 2240.0),
                ],
                "mark": 2240.0,
                "hosted_pnl": 0.0,
                "expected_qty": -1.0,
                "expected_avg": 2240.0,
                "expected_unrealized": 0.0,
                "expected_realized": 0.0,
            },
        ]

        store = RuntimeSummaryProjectionStore()
        for case in cases:
            with self.subTest(runtime_id=case["runtime_id"]):
                for lfc, qty, price in case["fills"]:
                    store.project_event(
                        self._ledger_fill(
                            runtime_id=case["runtime_id"],
                            binding_id=case["binding_id"],
                            symbol=case["symbol"],
                            quantity=qty,
                            price=price,
                            ledger_fill_count=lfc,
                            currency="TWD",
                            broker="tw-paper-sim",
                            created_at=f"2026-11-05T01:00:0{lfc}Z",
                        )
                    )
                summary = store.project_event(
                    self._mark_snapshot(
                        runtime_id=case["runtime_id"],
                        binding_id=case["binding_id"],
                        symbol=case["symbol"],
                        price=case["mark"],
                        pnl=case["hosted_pnl"],
                        fill_count=len(case["fills"]),
                    )
                )
                self.assertEqual(len(summary["positions"]), 1)
                row = summary["positions"][0]
                self.assertEqual(row["symbol"], case["symbol"])
                self.assertEqual(row["quantity"], case["expected_qty"])
                self.assertEqual(row["average_price"], case["expected_avg"])
                self.assertEqual(row["unrealized_pnl"], case["expected_unrealized"])
                self.assertEqual(row["realized_pnl"], case["expected_realized"])
                self.assertEqual(row["currency"], "TWD")
                self.assertEqual(row["broker_id"], "tw-paper-sim")
                if row["unrealized_pnl"] == 0.0:
                    self.assertEqual(math.copysign(1.0, row["unrealized_pnl"]), 1.0)
                if row["realized_pnl"] == 0.0:
                    self.assertEqual(math.copysign(1.0, row["realized_pnl"]), 1.0)
                self.assertLessEqual(
                    abs((row["unrealized_pnl"] + row["realized_pnl"]) - case["hosted_pnl"]),
                    1e-6,
                )

    def test_delivery_order_duplicate_out_of_order_reload_and_withholding(self) -> None:
        fill1 = self._ledger_fill(
            runtime_id="rt-order-001",
            binding_id="rb-order-001",
            symbol="2317.TWSE",
            quantity=-1.0,
            price=245.5,
            ledger_fill_count=1,
            created_at="2026-11-05T01:00:01Z",
        )
        fill2 = self._ledger_fill(
            runtime_id="rt-order-001",
            binding_id="rb-order-001",
            symbol="2317.TWSE",
            quantity=1.0,
            price=251.0,
            ledger_fill_count=2,
            created_at="2026-11-05T01:00:02Z",
        )
        fill3 = self._ledger_fill(
            runtime_id="rt-order-001",
            binding_id="rb-order-001",
            symbol="2317.TWSE",
            quantity=-1.0,
            price=240.0,
            ledger_fill_count=3,
            created_at="2026-11-05T01:00:03Z",
        )
        mark_ev = self._mark_snapshot(
            runtime_id="rt-order-001",
            binding_id="rb-order-001",
            symbol="2317.TWSE",
            price=235.0,
            fill_count=3,
        )

        # In-order 1 -> 2 -> 3 with duplicate ledger_fill_count ignored
        store_in_order = RuntimeSummaryProjectionStore()
        store_in_order.project_event(fill1)
        # Duplicate ledger_fill_count=1 with different event_id and bogus price is ignored
        dup_fill1 = dict(fill1, event_id="evt-dup-fill-1", metrics={**fill1["metrics"], "fill_price": 9999.0})
        store_in_order.project_event(dup_fill1)
        store_in_order.project_event(fill2)
        store_in_order.project_event(fill3)
        expected_summary = store_in_order.project_event(mark_ev)

        # Out-of-order 1 -> 3 -> (reload) -> 2
        with tempfile.TemporaryDirectory() as tmp_dir:
            store_path = Path(tmp_dir) / "runtime_summaries.json"
            store_ooo = RuntimeSummaryProjectionStore(path=store_path)
            store_ooo.project_event(fill1)
            after_3 = store_ooo.project_event(fill3)
            after_3_marked = store_ooo.project_event(
                self._mark_snapshot(
                    runtime_id="rt-order-001",
                    binding_id="rb-order-001",
                    symbol="2317.TWSE",
                    price=235.0,
                )
            )
            withheld_row = after_3_marked["positions"][0]
            self.assertNotIn("average_price", withheld_row)
            self.assertNotIn("realized_pnl", withheld_row)
            self.assertNotIn("unrealized_pnl", withheld_row)

            # Survives store reload while withheld
            reloaded_store = RuntimeSummaryProjectionStore(path=store_path)
            reloaded_while_withheld = reloaded_store.get("rt-order-001", tenant_id="tenant-dev")
            reloaded_row = reloaded_while_withheld["positions"][0]
            self.assertNotIn("average_price", reloaded_row)
            self.assertNotIn("realized_pnl", reloaded_row)
            self.assertNotIn("unrealized_pnl", reloaded_row)

            # Arrival of fill 2 closes the gap and produces exact same state as 1 -> 2 -> 3
            reloaded_store.project_event(fill2)
            final_ooo = reloaded_store.project_event(mark_ev)
            self.assertEqual(final_ooo["positions"], expected_summary["positions"])
            self.assertEqual(final_ooo["_cost_by_symbol"], expected_summary["_cost_by_symbol"])
            self.assertEqual(
                final_ooo["_cost_applied_fill_count"],
                expected_summary["_cost_applied_fill_count"],
            )
            self.assertNotIn("_cost_pending_fills", final_ooo)
            self.assertEqual(final_ooo["last_fill"], expected_summary["last_fill"])

        # Invalid fills permanently withhold cost for that binding (key absent, never zero)
        invalid_cases = [
            ("missing_lfc", self._ledger_fill(runtime_id="rt-bad-1", binding_id="rb-bad-1", ledger_fill_count=None)),
            ("bool_lfc", self._ledger_fill(runtime_id="rt-bad-2", binding_id="rb-bad-2", ledger_fill_count=True)),
            ("zero_lfc", self._ledger_fill(runtime_id="rt-bad-3", binding_id="rb-bad-3", ledger_fill_count=0)),
            ("zero_price", self._ledger_fill(runtime_id="rt-bad-4", binding_id="rb-bad-4", price=0.0)),
            ("neg_price", self._ledger_fill(runtime_id="rt-bad-5", binding_id="rb-bad-5", price=-10.0)),
            ("nan_price", self._ledger_fill(runtime_id="rt-bad-6", binding_id="rb-bad-6", price=float("nan"))),
            ("inf_price", self._ledger_fill(runtime_id="rt-bad-7", binding_id="rb-bad-7", price=float("inf"))),
            ("gap_at_1", self._ledger_fill(runtime_id="rt-bad-8", binding_id="rb-bad-8", ledger_fill_count=2)),
        ]
        for label, bad_fill in invalid_cases:
            with self.subTest(case=label):
                bad_store = RuntimeSummaryProjectionStore()
                bad_store.project_event(bad_fill)
                marked = bad_store.project_event(
                    self._mark_snapshot(
                        runtime_id=bad_fill["runtime_id"],
                        binding_id=bad_fill["binding_id"],
                        symbol=bad_fill["metadata"]["symbol"],
                        price=2240.0,
                    )
                )
                row = marked["positions"][0]
                self.assertNotIn("average_price", row)
                self.assertNotIn("realized_pnl", row)
                self.assertNotIn("unrealized_pnl", row)

    def test_currency_precedence_and_broker_id_across_all_row_branches(self) -> None:
        store = RuntimeSummaryProjectionStore()
        # Unmarked row carries fill metadata.currency and metadata.broker
        s1 = store.project_event(
            self._ledger_fill(
                runtime_id="rt-curr-1",
                binding_id="rb-curr-1",
                symbol="2330.TWSE",
                quantity=-1.0,
                price=2300.0,
                ledger_fill_count=1,
                currency="TWD",
                broker="tw-paper-sim",
            )
        )
        r1 = s1["positions"][0]
        self.assertEqual(r1["currency"], "TWD")
        self.assertEqual(r1["broker_id"], "tw-paper-sim")

        # Mark with quote_currency overrides fill metadata.currency
        s2 = store.project_event(
            self._mark_snapshot(
                runtime_id="rt-curr-1",
                binding_id="rb-curr-1",
                symbol="2330.TWSE",
                price=2250.0,
                quote_currency="USD",
            )
        )
        r2 = s2["positions"][0]
        self.assertEqual(r2["currency"], "USD")
        self.assertEqual(r2["broker_id"], "tw-paper-sim")

        # Mark without quote_currency preserves fill metadata.currency across fresh, stale, and failed mark branches
        store2 = RuntimeSummaryProjectionStore()
        store2.project_event(
            self._ledger_fill(
                runtime_id="rt-curr-2",
                binding_id="rb-curr-2",
                symbol="0050.TWSE",
                quantity=-2.0,
                price=100.0,
                ledger_fill_count=1,
                currency="TWD",
                broker="tw-paper-sim",
            )
        )
        fresh = store2.project_event(
            self._mark_snapshot(
                runtime_id="rt-curr-2",
                binding_id="rb-curr-2",
                symbol="0050.TWSE",
                price=98.0,
                quote_currency=None,
            )
        )
        self.assertEqual(fresh["positions"][0]["currency"], "TWD")
        self.assertEqual(fresh["positions"][0]["broker_id"], "tw-paper-sim")

        stale = store2.get(
            "rt-curr-2",
            tenant_id="tenant-dev",
            now=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(stale["positions"][0]["mark_stale"])
        self.assertEqual(stale["positions"][0]["currency"], "TWD")
        self.assertEqual(stale["positions"][0]["broker_id"], "tw-paper-sim")

        # Fill without currency or broker leaves keys absent (nothing inferred from symbol/market/pool)
        store3 = RuntimeSummaryProjectionStore()
        no_meta = store3.project_event(
            self._ledger_fill(
                runtime_id="rt-curr-3",
                binding_id="rb-curr-3",
                symbol="2330.TWSE",
                quantity=-1.0,
                price=2300.0,
                ledger_fill_count=1,
                currency=None,
                broker=None,
            )
        )
        self.assertNotIn("currency", no_meta["positions"][0])
        self.assertNotIn("broker_id", no_meta["positions"][0])

    def test_binding_rollover_clears_cost_currency_and_broker_state(self) -> None:
        store = RuntimeSummaryProjectionStore()
        store.project_event(
            self._ledger_fill(
                runtime_id="rt-roll-001",
                binding_id="rb-gen-1",
                symbol="2330.TWSE",
                quantity=-2.0,
                price=2300.0,
                ledger_fill_count=1,
                currency="TWD",
                broker="tw-paper-sim",
                created_at="2026-11-05T01:00:00Z",
            )
        )
        gen1 = store.project_event(
            self._mark_snapshot(
                runtime_id="rt-roll-001",
                binding_id="rb-gen-1",
                symbol="2330.TWSE",
                price=2250.0,
                created_at="2026-11-05T01:01:00Z",
            )
        )
        self.assertEqual(gen1["positions"][0]["average_price"], 2300.0)
        self.assertEqual(gen1["positions"][0]["currency"], "TWD")
        self.assertEqual(gen1["positions"][0]["broker_id"], "tw-paper-sim")

        # Rollover to rb-gen-2 via heartbeat clears all cost, currency, and broker state
        rolled = store.project_event(
            {
                "event_id": "evt-roll-hb",
                "event_type": "heartbeat",
                "created_at": "2026-11-05T02:00:00Z",
                "tenant_id": "tenant-dev",
                "environment": "paper",
                "deployment_stage": "paper",
                "runtime_id": "rt-roll-001",
                "binding_id": "rb-gen-2",
                "metrics": {"heartbeat": 1},
                "metadata": {"binding_effective_at": "2026-11-05T02:00:00Z"},
            }
        )
        for cleared_key in (
            "_cost_by_symbol",
            "_cost_applied_fill_count",
            "_cost_pending_fills",
            "_cost_withheld",
            "_currency_by_symbol",
            "_broker_by_symbol",
            "positions",
            "_positions_by_symbol",
        ):
            self.assertNotIn(cleared_key, rolled)

        # New generation starts empty at ledger_fill_count=1 without currency/broker unless supplied
        gen2 = store.project_event(
            self._ledger_fill(
                runtime_id="rt-roll-001",
                binding_id="rb-gen-2",
                symbol="2330.TWSE",
                quantity=1.0,
                price=2100.0,
                ledger_fill_count=1,
                currency=None,
                broker=None,
                created_at="2026-11-05T02:01:00Z",
            )
        )
        row2 = gen2["positions"][0]
        self.assertEqual(row2["quantity"], 1.0)
        self.assertEqual(row2["average_price"], 2100.0)
        self.assertEqual(row2["realized_pnl"], 0.0)
        self.assertNotIn("currency", row2)
        self.assertNotIn("broker_id", row2)

    def test_startup_backfill_rebuilds_once_and_withholds_on_gap_or_quantity_mismatch(self) -> None:
        import uuid
        from services.telemetry.trade_episode_projection import TradeEpisodeProjectionStore

        with tempfile.TemporaryDirectory() as tmp_dir:
            summary_path = Path(tmp_dir) / "runtime_summaries.json"
            ep_proj_path = Path(tmp_dir) / "trade_episode_projections.json"
            ep_events_path = Path(tmp_dir) / "trade_episode_events.json"

            ep_store = TradeEpisodeProjectionStore(ep_proj_path, ep_events_path)
            # Populate retained fills for rt-c98e4223 (including reversal fill stored under 2 episodes)
            ep1, ep2, ep3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
            fills_c98 = [
                (1, -1.0, 245.5, ep1, None),
                (2, 1.0, 251.0, ep1, None),
                (3, -1.0, 245.5, ep2, {"closed_episode_id": ep1, "close_quantity": 0.0, "opened_episode_id": ep2, "open_quantity": -1.0}),
                (4, -1.0, 240.0, ep2, None),
                (5, -1.0, 235.0, ep2, None),
            ]
            for lfc, qty, price, ep_id, rev in fills_c98:
                extra = {"reversal": rev} if rev else None
                ev = self._ledger_fill(
                    runtime_id="rt-c98e4223",
                    binding_id="rb-eda9b9a3379d4fb5a09f0e8432c84f20",
                    symbol="2317.TWSE",
                    quantity=qty,
                    price=price,
                    ledger_fill_count=lfc,
                    currency="TWD",
                    broker="tw-paper-sim",
                    created_at=f"2026-11-05T01:00:0{lfc}Z",
                    extra_metadata=extra,
                )
                ev["trade_episode_id"] = ep_id
                ev["persona_id"] = "persona-tw"
                ev["instrument_id"] = "2317.TWSE"
                ep_store.project_event(ev)

            # Populate incomplete fills (1 and 3, missing 2) for rt-gap
            for lfc in (1, 3):
                ev = self._ledger_fill(
                    runtime_id="rt-gap",
                    binding_id="rb-gap",
                    symbol="2330.TWSE",
                    quantity=-1.0,
                    price=2300.0,
                    ledger_fill_count=lfc,
                    currency="TWD",
                    broker="tw-paper-sim",
                    created_at=f"2026-11-05T01:00:0{lfc}Z",
                )
                ev["trade_episode_id"] = ep3
                ev["persona_id"] = "persona-tw"
                ev["instrument_id"] = "2330.TWSE"
                ep_store.project_event(ev)

            # Write pre-existing runtime_summaries.json without _cost_by_symbol
            saved_summaries = {
                '["tenant-dev","rt-c98e4223"]': {
                    "id": "rt-c98e4223",
                    "runtime_id": "rt-c98e4223",
                    "tenant_id": "tenant-dev",
                    "binding_id": "rb-eda9b9a3379d4fb5a09f0e8432c84f20",
                    "runtime_binding_id": "rb-eda9b9a3379d4fb5a09f0e8432c84f20",
                    "deployment_stage": "paper",
                    "environment": "paper",
                    "state": "active",
                    "last_event_at": "2026-11-05T02:00:00Z",
                    "_binding_latest_event_at": "2026-11-05T02:00:00Z",
                    "ledger_fill_count": 5,
                    "executed_trade_count": 5,
                    "_positions_by_symbol": {"2317.TWSE": -3.0},
                    "_marks_by_symbol": {
                        "2317.TWSE": {
                            "as_of": "2026-11-04T00:00:00Z",
                            "observed_at": "2026-11-04T06:00:00Z",
                            "lineage": {
                                "source_ids": ["tw-official:tw_price_daily:TWSE:2317:test"],
                                "connector_ids": ["tw-twse-tpex-official-market"],
                            },
                            "binding_id": "rb-eda9b9a3379d4fb5a09f0e8432c84f20",
                            "price": 235.0,
                            "source_ref": "source-ingest://snapshots/mss-2317",
                        }
                    },
                    "positions": [
                        {
                            "symbol": "2317.TWSE",
                            "quantity": -3.0,
                            "mark_price": 235.0,
                            "mark_as_of": "2026-11-04T00:00:00Z",
                            "mark_source_ref": "source-ingest://snapshots/mss-2317",
                            "market_value": -705.0,
                        }
                    ],
                    "position_count": 1,
                },
                '["tenant-dev","rt-gap"]': {
                    "id": "rt-gap",
                    "runtime_id": "rt-gap",
                    "tenant_id": "tenant-dev",
                    "binding_id": "rb-gap",
                    "runtime_binding_id": "rb-gap",
                    "deployment_stage": "paper",
                    "environment": "paper",
                    "state": "active",
                    "last_event_at": "2026-11-05T02:00:00Z",
                    "_binding_latest_event_at": "2026-11-05T02:00:00Z",
                    "ledger_fill_count": 3,
                    "executed_trade_count": 3,
                    "_positions_by_symbol": {"2330.TWSE": -2.0},
                    "_marks_by_symbol": {
                        "2330.TWSE": {
                            "as_of": "2026-11-04T00:00:00Z",
                            "observed_at": "2026-11-04T06:00:00Z",
                            "lineage": {
                                "source_ids": ["tw-official:tw_price_daily:TWSE:2330:test"],
                                "connector_ids": ["tw-twse-tpex-official-market"],
                            },
                            "binding_id": "rb-gap",
                            "price": 2240.0,
                            "source_ref": "source-ingest://snapshots/mss-2330",
                        }
                    },
                    "positions": [{"symbol": "2330.TWSE", "quantity": -2.0}],
                    "position_count": 1,
                },
            }
            summary_path.write_text(json.dumps(saved_summaries), encoding="utf-8")

            summary_store = RuntimeSummaryProjectionStore(path=summary_path)
            with self.assertLogs("services.telemetry.runtime_summary", level="WARNING") as cm:
                updated = summary_store.backfill_open_position_costs(ep_store)
            self.assertEqual(updated, 2)
            self.assertTrue(any("rt-gap" in msg for msg in cm.output))

            rebuilt_c98 = summary_store.get("rt-c98e4223", tenant_id="tenant-dev")
            row_c98 = rebuilt_c98["positions"][0]
            self.assertEqual(row_c98["average_price"], 240.166667)
            self.assertEqual(row_c98["unrealized_pnl"], 15.5)
            self.assertEqual(row_c98["realized_pnl"], -5.5)
            self.assertEqual(row_c98["currency"], "TWD")
            self.assertEqual(row_c98["broker_id"], "tw-paper-sim")

            rebuilt_gap = summary_store.get("rt-gap", tenant_id="tenant-dev")
            row_gap = rebuilt_gap["positions"][0]
            self.assertNotIn("average_price", row_gap)
            self.assertNotIn("realized_pnl", row_gap)
            self.assertNotIn("unrealized_pnl", row_gap)
            self.assertEqual(row_gap["currency"], "TWD")
            self.assertEqual(row_gap["broker_id"], "tw-paper-sim")

            # Second start changes nothing
            before_bytes = summary_path.read_text(encoding="utf-8")
            second_store = RuntimeSummaryProjectionStore(path=summary_path)
            second_updated = second_store.backfill_open_position_costs(ep_store)
            self.assertEqual(second_updated, 0)
            self.assertEqual(summary_path.read_text(encoding="utf-8"), before_bytes)


class TestInstrumentAwarePositionProjection(unittest.TestCase):
    """INSTRUMENT-AWARE-TELEMETRY-BFF-DRIFT-20261107 (AC 1).

    Fill shapes mirror ``paper_runtime._publish`` for ``paper_fill_simulated``:
    the event symbol is the underlying, the instrument identity rides in
    metadata, and a cash share fill predating the futures ledger carries none.
    """

    _FUTURE_ID = "ssf:QFF:202611"

    @classmethod
    def _fill(
        cls,
        *,
        lfc: int,
        quantity: float,
        price: float,
        symbol: str = "2330.TWSE",
        future: bool = False,
        margin_after: float = 0.0,
        carry_cash_instrument_fields: bool = False,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = TestPositionCostCurrencyBrokerProjection._ledger_fill(
            symbol=symbol,
            quantity=quantity,
            price=price,
            ledger_fill_count=lfc,
            broker="shioaji_paper",
        )["metadata"]
        meta["sec_type"] = "future" if future else "equity"
        if future:
            meta.update(
                {
                    "instrument_id": cls._FUTURE_ID,
                    "kind": "stock_future",
                    "multiplier": 100.0,
                    "contract_month": "202611",
                    "fee": 40.0,
                    "tax": 10.0,
                    "initial_margin_reserved": margin_after,
                }
            )
        elif carry_cash_instrument_fields:
            meta.update(
                {
                    "instrument_id": "eq:2330",
                    "kind": "cash_share",
                    "multiplier": 1.0,
                }
            )
        event = TestPositionCostCurrencyBrokerProjection._ledger_fill(
            symbol=symbol,
            quantity=quantity,
            price=price,
            ledger_fill_count=lfc,
            broker="shioaji_paper",
            created_at=f"2026-11-05T01:0{lfc}:00Z",
            extra_metadata={k: v for k, v in meta.items() if k not in ("symbol",)},
        )
        return event

    def _store(self) -> RuntimeSummaryProjectionStore:
        return RuntimeSummaryProjectionStore()

    def test_cash_share_fill_reproduces_hosted_1106_hand_numbers(self) -> None:
        store = self._store()
        store.project_event(self._fill(lfc=1, quantity=-5.0, price=2299.0))
        summary = store.project_event(
            TestPositionCostCurrencyBrokerProjection._mark_snapshot(
                symbol="2330.TWSE", price=2240.0, fill_count=1
            )
        )
        row = summary["positions"][0]
        self.assertEqual(row["quantity"], -5.0)
        self.assertEqual(row["average_price"], 2299.0)
        self.assertEqual(row["market_value"], -11200.0)
        self.assertEqual(row["unrealized_pnl"], 295.0)
        self.assertEqual(row["currency"], "TWD")
        # A cash share row from a fill without instrument fields is unchanged.
        for absent in ("instrument_id", "kind", "multiplier", "margin_reserved"):
            self.assertNotIn(absent, row)

    def test_stock_future_fill_scales_cost_and_realized_by_multiplier(self) -> None:
        store = self._store()
        store.project_event(self._fill(lfc=1, quantity=-5.0, price=2299.0))
        # Buy 2 contracts @ 2300, then sell 1 @ 2350 closing half.
        store.project_event(
            self._fill(lfc=2, quantity=2.0, price=2300.0, future=True, margin_after=92000.0)
        )
        summary = store.project_event(
            self._fill(lfc=3, quantity=-1.0, price=2350.0, future=True, margin_after=46000.0)
        )
        rows = {r.get("instrument_id", r["symbol"]): r for r in summary["positions"]}
        # The future never nets into the underlying cash share row.
        self.assertEqual(rows["2330.TWSE"]["quantity"], -5.0)
        future = rows[self._FUTURE_ID]
        self.assertEqual(future["symbol"], "2330.TWSE")
        self.assertEqual(future["kind"], "stock_future")
        self.assertEqual(future["quantity"], 1.0)
        self.assertEqual(future["multiplier"], 100.0)
        self.assertEqual(future["contract_month"], "202611")
        self.assertEqual(future["margin_reserved"], 46000.0)
        self.assertEqual(future["currency"], "TWD")
        self.assertEqual(future["average_price"], 2300.0)
        # realized = 1 contract * 100 * (2350 - 2300)
        self.assertEqual(future["realized_pnl"], 5000.0)

        # The futures mark is keyed by instrument_id but carries the same
        # Taiwan market-calendar provenance as its underlying's mark.
        mark_event = TestPositionCostCurrencyBrokerProjection._mark_snapshot(
            symbol="2330.TWSE", price=2360.0, fill_count=3
        )
        mark_event["metadata"]["mark_refs"][0]["symbol"] = self._FUTURE_ID
        marked = store.project_event(mark_event)
        future = next(
            r for r in marked["positions"] if r.get("instrument_id") == self._FUTURE_ID
        )
        # market value = 1 * 100 * 2360 ; unrealized = 1 * 100 * (2360 - 2300)
        self.assertEqual(future["market_value"], 236000.0)
        self.assertEqual(future["unrealized_pnl"], 6000.0)
        cash = next(r for r in marked["positions"] if "instrument_id" not in r)
        self.assertEqual(cash["quantity"], -5.0)
        self.assertNotIn("market_value", cash)

        group = marked["positions_by_underlying"]["2330.TWSE"]
        self.assertEqual(group["net_delta_units"], -5.0 + 100.0)
        self.assertEqual(
            sorted(i.get("instrument_id", "cash") for i in group["instruments"]),
            ["cash", self._FUTURE_ID],
        )

    def test_cash_fill_carrying_instrument_fields_keeps_symbol_key(self) -> None:
        store = self._store()
        summary = store.project_event(
            self._fill(
                lfc=1,
                quantity=-5.0,
                price=2299.0,
                carry_cash_instrument_fields=True,
            )
        )
        row = summary["positions"][0]
        self.assertEqual(row["instrument_id"], "eq:2330")
        self.assertEqual(row["kind"], "cash_share")
        self.assertEqual(row["multiplier"], 1.0)
        self.assertNotIn("margin_reserved", row)
        self.assertEqual(row["average_price"], 2299.0)
        self.assertIn("2330.TWSE", summary["_cost_by_symbol"])

    def test_future_flip_realizes_only_closed_contracts_times_multiplier(self) -> None:
        store = self._store()
        store.project_event(
            self._fill(lfc=1, quantity=1.0, price=2300.0, future=True, margin_after=46000.0)
        )
        summary = store.project_event(
            self._fill(lfc=2, quantity=-3.0, price=2290.0, future=True, margin_after=92000.0)
        )
        row = summary["positions"][0]
        self.assertEqual(row["quantity"], -2.0)
        self.assertEqual(row["average_price"], 2290.0)
        # closes 1 long contract: 1 * 100 * (2290 - 2300)
        self.assertEqual(row["realized_pnl"], -1000.0)
        self.assertEqual(row["margin_reserved"], 92000.0)

    def test_expiry_settlement_closes_the_contract_and_books_realized(self) -> None:
        store = self._store()
        store.project_event(self._fill(lfc=1, quantity=-5.0, price=2299.0))
        store.project_event(
            self._fill(lfc=2, quantity=2.0, price=2300.0, future=True, margin_after=92000.0)
        )
        settled = TestPositionCostCurrencyBrokerProjection._ledger_fill(
            symbol="2330.TWSE",
            quantity=0.0,
            price=2310.0,
            ledger_fill_count=None,
            created_at="2026-11-18T05:30:00Z",
            event_id="evt-settle-001",
            extra_metadata={
                "instrument_id": self._FUTURE_ID,
                "kind": "stock_future",
                "contract": "QFF",
                "contract_month": "202611",
                "multiplier": 100.0,
                "settled_quantity": 2.0,
                "final_settlement_price": 2310.0,
                # (2310 - 2300) * 2 contracts * 100
                "realized_pnl": 2000.0,
                "settlement_fee": 5.0,
                "fee": 10.0,
                "tax": 4.0,
                "price_basis": "FinalSettlementPriceSSF",
            },
        )
        settled["event_type"] = "paper_futures_settled"
        settled["metrics"] = {}
        summary = store.project_event(settled)
        # The cash share short is untouched; the contract is gone.
        self.assertEqual([r["symbol"] for r in summary["positions"]], ["2330.TWSE"])
        self.assertNotIn("instrument_id", summary["positions"][0])
        self.assertEqual(summary["executed_trade_count"], 2)
        self.assertEqual(
            summary["_cost_by_symbol"][self._FUTURE_ID]["realized_pnl"], 2000.0
        )
        # A redelivered settlement does not book realized twice.
        again = store.project_event(dict(settled))
        self.assertEqual(
            again["_cost_by_symbol"][self._FUTURE_ID]["realized_pnl"], 2000.0
        )


class TestInstrumentCounterfactualRows(unittest.TestCase):
    """INSTRUMENT-DECISION-LEARNING-COUNTERFACTUAL-20261107 (AC 3)."""

    @staticmethod
    def _row_event(row: dict[str, Any], *, event_id: str) -> dict[str, Any]:
        event = TestPositionCostCurrencyBrokerProjection._ledger_fill(
            symbol="0050.TWSE",
            quantity=0.0,
            price=1.0,
            ledger_fill_count=None,
            created_at="2026-11-09T05:30:00Z",
            event_id=event_id,
            extra_metadata=dict(row),
        )
        event["event_type"] = "persona_instrument_counterfactual"
        event["metrics"] = {}
        return event

    def test_rows_fold_keyed_by_decision_and_instrument_and_leave_cash_summary_alone(self) -> None:
        from services.execution.lean_runtime import expression_replay as er
        from services.execution.lean_runtime.test_expression_replay import _cash, _fut, _record

        record = _record(
            "d-0050", "0050", "SHORT", 1.0,
            [_cash("0050", 104.95, 952), _fut("ssf:SRF:202611", "0050", 1000, 104.95, 8700.0, 2)],
            300000.0, {"eq:0050": [92.5], "ssf:SRF:202611": [92.5]},
        )
        result = er.replay([record], None)["decisions"][0]
        rows = er.counterfactual_rows(record, result)

        store = RuntimeSummaryProjectionStore()
        base = store.project_event(TestInstrumentAwarePositionProjection._fill(lfc=1, quantity=-5.0, price=2299.0))
        before = {k: v for k, v in base.items() if k not in ("health_summary",)}
        for index, row in enumerate(rows):
            summary = store.project_event(self._row_event(row, event_id=f"evt-cf-{index}"))
        # Redelivery of the same (decision, instrument) overwrites, never duplicates.
        summary = store.project_event(self._row_event(rows[0], event_id="evt-cf-redelivered"))

        stored = summary["instrument_counterfactuals"]
        self.assertEqual([(r["decision_id"], r["instrument_id"]) for r in stored],
                         [("d-0050", "eq:0050"), ("d-0050", "ssf:SRF:202611")])
        chosen = next(r for r in stored if r["chosen"])
        self.assertEqual((chosen["instrument_id"], chosen["pnl_cf"], chosen["units"]), ("ssf:SRF:202611", 24900.0, 2))
        self.assertEqual(stored[0]["event_id"], "evt-cf-redelivered")
        # Fills, positions, trade counts and cost basis are untouched.
        after = {k: v for k, v in summary.items() if k not in ("health_summary", "instrument_counterfactuals")}
        for key in ("positions", "position_count", "executed_trade_count", "ledger_fill_count", "_cost_by_symbol"):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertNotIn("instrument_counterfactuals", base)

    def test_row_without_decision_or_instrument_key_is_ignored(self) -> None:
        store = RuntimeSummaryProjectionStore()
        summary = store.project_event(self._row_event({"decision_id": "d-1"}, event_id="evt-bad"))
        self.assertNotIn("instrument_counterfactuals", summary)
