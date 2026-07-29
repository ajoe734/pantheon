"""Tests for telemetry-owned runtime status projection."""

from __future__ import annotations

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
