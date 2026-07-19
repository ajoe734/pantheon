from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import mock

import jsonschema


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]


def _load_scheduler_module():
    sys.modules.pop("reconciliation_drift_scheduler_test", None)
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_scheduler_test",
        SERVICE_DIR / "scheduler_worker.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconciliation_drift_scheduler_test"] = module
    spec.loader.exec_module(module)
    return module


def _load_service_module(data_dir: str):
    sys.modules.pop("consumer", None)
    sys.modules.pop("store", None)
    sys.modules.pop("reconciliation_drift_sched_test_main", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "reconciliation_drift_sched_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["reconciliation_drift_sched_test_main"] = module
        with mock.patch.dict(
            "os.environ",
            {
                "RECONCILIATION_DRIFT_DATA_DIR": data_dir,
                "RECONCILIATION_DRIFT_STORE_BACKEND": "json",
                "PERSISTENCE_POSTURE": "lenient",
                "PANTHEON_TELEMETRY_API_URL": "",
            },
        ):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("consumer", None)
        sys.modules.pop("store", None)


def _healthy_runtime_summary(**overrides):
    summary = {
        "binding_id": "rtb-sched-healthy-001",
        "runtime_id": "runtime-sched-healthy-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-sched-001",
        "capital_pool_id": "pool-sched-001",
        "persona_capital_binding_id": "pcb-sched-001",
        "artifact_id": "artifact-sched-001",
        "artifact_version": "1.0.0",
        "trace_id": "trace-sched-001",
        "last_event_id": "evt-sched-001",
        "last_heartbeat_event_id": "evt-heartbeat-sched-001",
        "state": "active",
        "health_summary": {
            "paper_runtime": "ok",
            "bridge": "ok",
            "telemetry": "ok",
            "broker": "not_applicable",
        },
        "queue_lag_ms": 10,
        "event_delivery_lag_ms": 20,
        "avg_slippage_bps": 1.5,
        "baseline_metrics": {"avg_slippage_bps": 1.5},
    }
    summary.update(overrides)
    return summary


def _paper_lifecycle_summary(**overrides):
    upstream_event_id = "11111111-1111-4111-8111-111111111111"
    trace_id = "22222222-2222-4222-8222-222222222222"
    binding_id = "33333333-3333-4333-8333-333333333333"
    summary = _healthy_runtime_summary(
        binding_id=binding_id,
        runtime_id="runtime-paper-lifecycle-001",
        deployment_stage="paper",
        deployment_plan_id="plan-paper-lifecycle-001",
        capital_pool_id="pool-paper-lifecycle-001",
        persona_capital_binding_id="pcb-paper-lifecycle-001",
        artifact_id="artifact-paper-lifecycle-001",
        artifact_version="1.0.0",
        trace_id=trace_id,
        last_event_id=upstream_event_id,
        last_lifecycle_identity={
            "event_id": upstream_event_id,
            "event_type": "position_snapshot",
            "created_at": "2026-07-15T10:00:00Z",
            "tenant_id": "tenant-paper-001",
            "environment": "paper",
            "execution_mode": "paper",
            "deployment_stage": "paper",
            "binding_id": binding_id,
            "runtime_id": "runtime-paper-lifecycle-001",
            "capital_pool_id": "pool-paper-lifecycle-001",
            "artifact_id": "artifact-paper-lifecycle-001",
            "artifact_version": "1.0.0",
            "plan_id": "plan-paper-lifecycle-001",
            "persona_capital_binding_id": "pcb-paper-lifecycle-001",
            "trace_id": trace_id,
            "signal_id": "signal-paper-lifecycle-001",
            "run_id": "run-paper-lifecycle-001",
            "loop_run_id": "lr-paper-lifecycle-001",
            "aggregate_type": "trade_journey",
            "aggregate_id": "tj-paper-lifecycle-001",
            "sequence_no": 6,
            "causal_parent_id": "fill-paper-lifecycle-001",
            "source_mode": "live",
            "target": {
                "strategy_id": "strategy-paper-lifecycle-001",
                "artifact_version": "1.0.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "authority_refs": {
                "write_owner": "runtime-manager",
                "authority_source": "runtime_binding",
                "runtime_role": "paper",
                "runtime_mode": "paper",
                "persona_id": "persona-paper-lifecycle-001",
            },
            "metadata": {
                "signal_id": "signal-paper-lifecycle-001",
                "run_id": "run-paper-lifecycle-001",
                "sequence_no": 6,
            },
            "correlation_envelope": {
                "schema_version": "trade-journey-envelope/1",
                "tenant_id": "tenant-paper-001",
                "environment": "paper",
                "journey_id": "tj-paper-lifecycle-001",
                "correlation_id": "44444444-4444-4444-8444-444444444444",
                "trace_id": trace_id,
                "event_id": upstream_event_id,
                "causation_event_id": "fill-paper-lifecycle-001",
                "producer": "execution.paper_runtime",
                "event_time": "2026-07-15T10:00:00Z",
                "received_at": "2026-07-15T10:00:00Z",
                "producer_revision": 1,
            },
        },
    )
    summary.update(overrides)
    return summary


# ---------------------------------------------------------------------------
# scheduler_worker unit tests
# ---------------------------------------------------------------------------


def test_scheduler_run_tick_posts_to_scheduled_reconcile() -> None:
    from fastapi.testclient import TestClient

    scheduler = _load_scheduler_module()

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        with mock.patch.object(scheduler, "run_tick") as mock_tick:
            mock_tick.return_value = {"status": "ok", "tick_id": "t1", "evaluated_binding_count": 0}
            result = scheduler.run_tick(api_url="http://test", tick_id="t1")
            assert result["status"] == "ok"


def test_scheduler_env_vars_respected() -> None:
    scheduler = _load_scheduler_module()

    with mock.patch.dict("os.environ", {"RECONCILIATION_DRIFT_SCHEDULER_INTERVAL_SECONDS": "120",
                                         "RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS": "2",
                                         "RECONCILIATION_DRIFT_URL": "http://custom:9999"}):
        assert scheduler._env_int("RECONCILIATION_DRIFT_SCHEDULER_MAX_TICKS", 0) == 2


# ---------------------------------------------------------------------------
# /api/reconciliation-drift/scheduled-reconcile endpoint tests
# ---------------------------------------------------------------------------


def test_scheduled_reconcile_empty_telemetry() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        # 1. Telemetry API URL is empty -> failure status (unavailable)
        with mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": ""}):
            with mock.patch.object(svc, "PANTHEON_TELEMETRY_API_URL", "", create=True):
                resp = client.post(
                    "/api/reconciliation-drift/scheduled-reconcile",
                    json={"tick_id": "tick-test-001"},
                )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["status"] == "failure"
        assert payload["tick_id"] == "tick-test-001"
        assert payload["evaluated_binding_count"] == 0
        assert payload["skipped_binding_count"] == 0
        assert payload["evaluation_ids"] == []

        # 2. Telemetry API URL configured but returns empty summaries -> degraded status
        with mock.patch.object(svc, "_fetch_telemetry_runtime_summaries", return_value=[]):
            resp2 = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-test-002"},
            )
        assert resp2.status_code == 201
        payload2 = resp2.json()
        assert payload2["status"] == "degraded"
        assert payload2["tick_id"] == "tick-test-002"
        assert payload2["evaluated_binding_count"] == 0


def test_scheduled_reconcile_never_marks_incomplete_actual_state_green() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        fake_summaries = [
            {
                "binding_id": "rtb-sched-test-001",
                "runtime_id": "runtime-sched-001",
                "last_event_id": "evt-001",
            }
        ]

        with mock.patch.object(svc, "_fetch_telemetry_runtime_summaries", return_value=fake_summaries):
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-test-002"},
            )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["status"] == "degraded"
        assert payload["tick_id"] == "tick-test-002"
        assert payload["evaluated_binding_count"] == 1
        assert payload["skipped_binding_count"] == 0
        assert len(payload["evaluation_ids"]) == 1

        # Verify evaluation records are persisted with the right fields
        listed = client.get("/api/reconciliation-drift/evaluations",
                            params={"binding_id": "rtb-sched-test-001"})
        assert listed.status_code == 200
        evaluations = listed.json()
        assert len(evaluations) == 1
        ev = evaluations[0]
        assert ev["binding_id"] == "rtb-sched-test-001"
        assert ev["runtime_id"] == "runtime-sched-001"
        assert ev["tick_id"] == "tick-test-002"
        assert ev["trigger"] == "scheduled"
        assert ev["status"] == "degraded"
        checks = {check["check"]: check for check in ev["reconciliation_checks"]}
        assert checks["authoritative_actual_identity"]["status"] == "ok"
        assert checks["actual_metrics_presence"]["status"] == "degraded"
        assert checks["queue_lag_ms"]["status"] == "degraded"
        assert checks["event_delivery_lag_ms"]["status"] == "degraded"


def test_scheduled_reconcile_uses_authoritative_health_and_lag_for_green() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        with mock.patch.object(
            svc,
            "_fetch_telemetry_runtime_summaries",
            return_value=[_healthy_runtime_summary()],
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-healthy-001"},
            )

        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["drift_report_ids"] == []
        evaluation = client.get(
            "/api/reconciliation-drift/evaluations",
            params={"binding_id": "rtb-sched-healthy-001"},
        ).json()[0]
        assert evaluation["status"] == "ok"
        assert all(
            check["status"] == "ok" for check in evaluation["reconciliation_checks"]
        )


def test_scheduled_reconcile_can_target_one_binding() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        target = _healthy_runtime_summary(
            binding_id="rtb-sched-target-001",
            runtime_id="runtime-sched-target-001",
            last_event_id="evt-sched-target-001",
        )
        other = _healthy_runtime_summary(
            binding_id="rtb-sched-other-001",
            runtime_id="runtime-sched-other-001",
            last_event_id="evt-sched-other-001",
        )

        with mock.patch.object(
            svc,
            "_fetch_telemetry_runtime_summaries",
            return_value=[other, target],
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": "tick-target-binding-001",
                    "binding_id": "rtb-sched-target-001",
                },
            )

        assert response.status_code == 201
        payload = response.json()
        assert payload["evaluated_binding_count"] == 1
        assert payload["skipped_binding_count"] == 0
        assert payload["telemetry_summaries_fetched"] == 1
        assert payload["evaluation_ids"] == [
            svc._tick_evaluation_id(
                "tick-target-binding-001",
                "rtb-sched-target-001",
            )
        ]
        assert (
            svc.store.get_evaluation(
                svc._tick_evaluation_id(
                    "tick-target-binding-001",
                    "rtb-sched-other-001",
                )
            )
            is None
        )


def test_scheduled_reconcile_can_skip_incident_dispatch_for_targeted_probe() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        target = _healthy_runtime_summary(
            binding_id="rtb-sched-target-dispatch-001",
            runtime_id="runtime-sched-target-dispatch-001",
            last_event_id="evt-sched-target-dispatch-001",
            queue_lag_ms=20_000,
        )

        with (
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[target],
            ),
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                side_effect=AssertionError("incident dispatch should be skipped"),
            ) as classify,
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": "tick-target-no-incident-001",
                    "binding_id": "rtb-sched-target-dispatch-001",
                    "dispatch_incidents": False,
                },
            )

        assert response.status_code == 201
        payload = response.json()
        assert payload["incident_dispatch_enabled"] is False
        assert payload["evaluated_binding_count"] == 1
        assert payload["drift_report_ids"] == []
        assert payload["incident_ids"] == []
        assert payload["incident_delivery_errors"] == []
        assert classify.call_count == 0


def test_scheduled_reconcile_lifecycle_only_appends_without_evaluation_store() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _paper_lifecycle_summary()
        binding_id = summary["binding_id"]
        tick_id = "tick-target-lifecycle-only-001"
        evaluation_id = svc._tick_evaluation_id(tick_id, binding_id)

        with (
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[summary],
            ),
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                return_value={
                    "status": "accepted",
                    "terminal": True,
                    "retryable": False,
                    "outcome": "accepted",
                    "http_status": 202,
                    "response": {"status": "accepted"},
                    "error": None,
                },
            ) as append,
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                side_effect=AssertionError("incident dispatch should be skipped"),
            ),
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id,
                    "binding_id": binding_id,
                    "dispatch_incidents": False,
                    "lifecycle_only": True,
                },
            )

        assert response.status_code == 201
        payload = response.json()
        assert payload["lifecycle_only"] is True
        assert payload["incident_dispatch_enabled"] is False
        assert payload["evaluation_ids"] == [evaluation_id]
        assert payload["lifecycle_append_results"][0]["binding_id"] == binding_id
        assert payload["lifecycle_append_results"][0]["status"] == "accepted"
        assert payload["lifecycle_accepted_event_ids"] == [
            payload["lifecycle_append_results"][0]["event_id"]
        ]
        assert svc.store.get_evaluation(evaluation_id) is None
        append.assert_called_once()


def test_scheduled_lag_breach_creates_deterministic_report_and_dedup_incident() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _healthy_runtime_summary(queue_lag_ms=20_000)
        with (
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[summary],
            ),
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                return_value={"incident_id": "inc-drift-sched-001"},
            ) as classify,
        ):
            first = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-lag-001"},
            )
            duplicate_tick = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-lag-001"},
            )
            next_tick = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-lag-002"},
            )

        first_payload = first.json()
        assert first_payload["status"] == "critical"
        assert first_payload["incident_ids"] == ["inc-drift-sched-001"]
        assert first_payload["incident_delivery_errors"] == []
        assert duplicate_tick.json()["drift_report_ids"] == first_payload["drift_report_ids"]
        assert next_tick.json()["drift_report_ids"] == first_payload["drift_report_ids"]
        assert next_tick.json()["incident_ids"] == ["inc-drift-sched-001"]
        assert classify.call_count == 2
        assert len(svc.store.list_drift_reports()) == 1


def test_scheduled_same_tick_replays_retryable_incident_delivery() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _healthy_runtime_summary(event_delivery_lag_ms=40_000)
        with (
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[summary],
            ),
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                side_effect=[
                    svc.HTTPException(status_code=502, detail="incidents unavailable"),
                    {"incident_id": "inc-drift-replayed-001"},
                ],
            ) as classify,
        ):
            failed = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-delivery-retry-001"},
            )
            replayed = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-delivery-retry-001"},
            )

        assert failed.json()["status"] == "failure"
        assert failed.json()["incident_delivery_errors"][0]["status_code"] == 502
        assert replayed.json()["status"] == "critical"
        assert replayed.json()["incident_ids"] == ["inc-drift-replayed-001"]
        assert classify.call_count == 2
        evaluation = svc.store.get_evaluation(
            svc._tick_evaluation_id("tick-delivery-retry-001", summary["binding_id"])
        )
        assert evaluation["incident_delivery"]["status"] == "delivered"


def test_scheduled_reconcile_idempotent_same_tick_id() -> None:
    """Duplicate ticks with same tick_id must not create duplicate evaluation records."""
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        fake_summaries = [
            {
                "binding_id": "rtb-idem-001",
                "runtime_id": "runtime-idem-001",
                "telemetry_event_ids": ["evt-idem-001"],
                "observed_metrics": {},
                "baseline_metrics": {},
            }
        ]

        with mock.patch.object(svc, "_fetch_telemetry_runtime_summaries", return_value=fake_summaries):
            first = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-idem-001"},
            )
            assert first.status_code == 201
            assert first.json()["evaluated_binding_count"] == 1
            assert first.json()["skipped_binding_count"] == 0

            second = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-idem-001"},
            )
            assert second.status_code == 201
            payload = second.json()
            assert payload["evaluated_binding_count"] == 0
            assert payload["skipped_binding_count"] == 1
            assert "rtb-idem-001" in payload["skipped_binding_ids"]

        # Exactly one evaluation record exists
        listed = client.get("/api/reconciliation-drift/evaluations",
                            params={"binding_id": "rtb-idem-001"})
        assert len(listed.json()) == 1


def test_scheduled_reconcile_normalizes_last_event_id_fields() -> None:
    """Real telemetry runtime-summary exposes last_event_id / last_heartbeat_event_id,
    not telemetry_event_ids.  The scheduled reconciler must normalise both forms so
    event evidence is always linked in the evaluation record."""
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        fake_summaries = [
            {
                "binding_id": "rtb-norm-001",
                "runtime_id": "runtime-norm-001",
                # Real contract fields — no telemetry_event_ids list present
                "last_event_id": "evt-last-001",
                "last_heartbeat_event_id": "evt-hb-001",
                "observed_metrics": {},
                "baseline_metrics": {},
            },
            {
                "binding_id": "rtb-norm-002",
                "runtime_id": "runtime-norm-002",
                # Only last_event_id; last_heartbeat_event_id absent
                "last_event_id": "evt-last-002",
                "observed_metrics": {},
                "baseline_metrics": {},
            },
            {
                "binding_id": "rtb-norm-003",
                "runtime_id": "runtime-norm-003",
                # last_event_id == last_heartbeat_event_id — should be deduplicated
                "last_event_id": "evt-same-003",
                "last_heartbeat_event_id": "evt-same-003",
                "observed_metrics": {},
                "baseline_metrics": {},
            },
        ]

        with mock.patch.object(svc, "_fetch_telemetry_runtime_summaries", return_value=fake_summaries):
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-norm-001"},
            )
        assert resp.status_code == 201
        assert resp.json()["evaluated_binding_count"] == 3

        ev1 = client.get("/api/reconciliation-drift/evaluations",
                         params={"binding_id": "rtb-norm-001"}).json()[0]
        check1 = ev1["reconciliation_checks"][0]
        assert set(check1["telemetry_event_ids"]) == {"evt-last-001", "evt-hb-001"}

        ev2 = client.get("/api/reconciliation-drift/evaluations",
                         params={"binding_id": "rtb-norm-002"}).json()[0]
        check2 = ev2["reconciliation_checks"][0]
        assert check2["telemetry_event_ids"] == ["evt-last-002"]

        ev3 = client.get("/api/reconciliation-drift/evaluations",
                         params={"binding_id": "rtb-norm-003"}).json()[0]
        check3 = ev3["reconciliation_checks"][0]
        # Deduplicated when last_event_id == last_heartbeat_event_id
        assert check3["telemetry_event_ids"] == ["evt-same-003"]


def test_scheduled_reconcile_different_tick_ids_create_separate_records() -> None:
    """Different tick_ids for same binding create separate evaluation records."""
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        fake_summaries = [
            {"binding_id": "rtb-multi-001", "runtime_id": "runtime-multi-001",
             "telemetry_event_ids": [], "observed_metrics": {}, "baseline_metrics": {}}
        ]

        with mock.patch.object(svc, "_fetch_telemetry_runtime_summaries", return_value=fake_summaries):
            r1 = client.post("/api/reconciliation-drift/scheduled-reconcile", json={"tick_id": "tick-A"})
            r2 = client.post("/api/reconciliation-drift/scheduled-reconcile", json={"tick_id": "tick-B"})

        assert r1.json()["evaluated_binding_count"] == 1
        assert r2.json()["evaluated_binding_count"] == 1

        listed = client.get("/api/reconciliation-drift/evaluations",
                            params={"binding_id": "rtb-multi-001"})
        assert len(listed.json()) == 2


def test_scheduled_evaluation_id_preserves_long_tick_and_binding_identity() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        shared_tick_prefix = "loop-prod-tel-002-run-rb-d4a79ff7e2e94f188f63e87a38b347a5-"
        first_tick = shared_tick_prefix + "2026-07-19T16:34:08Z-1"
        second_tick = shared_tick_prefix + "2026-07-19T18:21:11Z-1"
        shared_binding_prefix = "rb-d4a79ff7e2e94f188f63e87a38b347a5-"
        first_binding = shared_binding_prefix + "primary"
        second_binding = shared_binding_prefix + "secondary"

        assert first_tick[:32] == second_tick[:32]
        assert first_binding[:24] == second_binding[:24]
        first_id = svc._tick_evaluation_id(first_tick, first_binding)

        assert first_id == svc._tick_evaluation_id(first_tick, first_binding)
        assert first_id != svc._tick_evaluation_id(second_tick, first_binding)
        assert first_id != svc._tick_evaluation_id(first_tick, second_binding)


def _accepted_delivery() -> dict:
    return {
        "status": "accepted",
        "terminal": True,
        "retryable": False,
        "outcome": "accepted",
        "http_status": 202,
        "response": {"status": "accepted"},
        "error": None,
    }


def test_scheduled_reconcile_appends_identity_consistent_paper_lifecycle_event() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _paper_lifecycle_summary()
        with (
            mock.patch.dict(
                "os.environ",
                {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"},
            ),
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[summary],
            ),
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                return_value=_accepted_delivery(),
            ) as append,
        ):
            first = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-paper-lifecycle-001"},
            )
            duplicate = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-paper-lifecycle-001"},
            )

        assert first.status_code == 201
        assert first.json()["status"] == "ok"
        assert append.call_count == 1
        telemetry_url, event = append.call_args.args
        assert telemetry_url == "http://telemetry:8083"
        assert event["event_type"] == "reconciliation_completed"
        assert str(uuid.UUID(event["event_id"])) == event["event_id"]
        assert event["binding_id"] == summary["binding_id"]
        assert event["runtime_id"] == summary["runtime_id"]
        assert event["signal_id"] == "signal-paper-lifecycle-001"
        assert event["run_id"] == "run-paper-lifecycle-001"
        assert event["loop_run_id"] == "lr-paper-lifecycle-001"
        assert event["aggregate_id"] == "tj-paper-lifecycle-001"
        assert event["authority_refs"]["persona_id"] == "persona-paper-lifecycle-001"
        assert event["metadata"]["persona_id"] == "persona-paper-lifecycle-001"
        assert event["sequence_no"] == 7
        assert event["causal_parent_id"] == summary["last_lifecycle_identity"]["event_id"]
        assert event["correlation_envelope"]["journey_id"] == "tj-paper-lifecycle-001"
        assert (
            event["correlation_envelope"]["causation_event_id"]
            == summary["last_lifecycle_identity"]["event_id"]
        )

        telemetry_schema = json.loads(
            (_REPO_ROOT / "services/telemetry/telemetry_event.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.validate(
            event,
            telemetry_schema,
            format_checker=jsonschema.FormatChecker(),
        )

        evaluation_id = svc._tick_evaluation_id(
            "tick-paper-lifecycle-001", summary["binding_id"]
        )
        evaluation = svc.store.get_evaluation(evaluation_id)
        delivery = evaluation["lifecycle_append"]
        assert delivery["status"] == "accepted"
        assert delivery["terminal"] is True
        assert delivery["retryable"] is False
        assert delivery["attempt_count"] == 1
        assert delivery["event"] == event
        assert first.json()["lifecycle_accepted_event_ids"] == [event["event_id"]]
        assert duplicate.json()["lifecycle_accepted_event_ids"] == [event["event_id"]]


def test_scheduled_reconcile_retries_same_event_after_ambiguous_delivery() -> None:
    from fastapi.testclient import TestClient

    retryable = {
        "status": "retryable_error",
        "terminal": False,
        "retryable": True,
        "outcome": "ambiguous",
        "http_status": None,
        "response": None,
        "error": "connection reset after send",
    }
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _paper_lifecycle_summary()
        with (
            mock.patch.dict(
                "os.environ",
                {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"},
            ),
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[summary],
            ),
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                side_effect=[retryable, _accepted_delivery()],
            ) as append,
        ):
            failed = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-paper-retry-001"},
            )
            recovered = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-paper-retry-001"},
            )

        assert failed.json()["status"] == "failure"
        assert failed.json()["lifecycle_retryable_errors"][0]["terminal"] is False
        assert failed.json()["lifecycle_retryable_errors"][0]["retryable"] is True
        assert recovered.json()["status"] == "ok"
        assert append.call_count == 2
        first_event = append.call_args_list[0].args[1]
        retried_event = append.call_args_list[1].args[1]
        assert retried_event == first_event

        evaluation = svc.store.get_evaluation(
            svc._tick_evaluation_id("tick-paper-retry-001", summary["binding_id"])
        )
        delivery = evaluation["lifecycle_append"]
        assert delivery["status"] == "accepted"
        assert delivery["attempt_count"] == 2
        assert len(svc.store.list_evaluations()) == 1


def test_scheduled_reconcile_waits_for_accepted_append_visibility_without_periodic_loop() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        stale_summary = _paper_lifecycle_summary()
        with (
            mock.patch.dict(
                "os.environ",
                {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"},
            ),
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[stale_summary],
            ) as fetch_summaries,
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                return_value=_accepted_delivery(),
            ) as append,
        ):
            accepted = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-visibility-001"},
            )
            deferred = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-visibility-002"},
            )

            assert accepted.json()["status"] == "ok"
            assert deferred.json()["status"] == "failure"
            assert append.call_count == 1
            deferred_receipt = deferred.json()["lifecycle_retryable_errors"][0]
            assert deferred_receipt["status"] == "deferred"
            assert (
                deferred_receipt["reason"]
                == "accepted_lifecycle_append_not_visible"
            )

            first_reconciliation = json.loads(
                json.dumps(append.call_args_list[0].args[1])
            )
            caught_up_summary = json.loads(json.dumps(stale_summary))
            caught_up_summary["last_event_id"] = first_reconciliation["event_id"]
            caught_up_summary["last_lifecycle_identity"] = first_reconciliation
            fetch_summaries.return_value = [caught_up_summary]

            caught_up = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-visibility-002"},
            )

            # Projector catch-up acknowledges visibility but the reconciliation
            # event itself must never become the parent of another periodic
            # reconciliation event.
            assert caught_up.json()["status"] == "ok"
            assert append.call_count == 1
            caught_up_receipt = caught_up.json()["lifecycle_append_results"][0]
            assert caught_up_receipt["status"] == "not_eligible"
            assert caught_up_receipt["reason"] == "lifecycle_already_reconciled"

            accepted_evaluation = svc.store.get_evaluation(
                svc._tick_evaluation_id(
                    "tick-visibility-001", stale_summary["binding_id"]
                )
            )
            assert (
                accepted_evaluation["lifecycle_append"][
                    "summary_visibility_event_id"
                ]
                == first_reconciliation["event_id"]
            )

            next_lifecycle = json.loads(json.dumps(first_reconciliation))
            next_event_id = "88888888-8888-4888-8888-888888888888"
            next_lifecycle.update(
                {
                    "event_id": next_event_id,
                    "event_type": "position_snapshot",
                    "created_at": "2026-07-15T10:00:08Z",
                    "sequence_no": first_reconciliation["sequence_no"] + 1,
                    "causal_parent_id": first_reconciliation["event_id"],
                }
            )
            next_lifecycle.pop("reconciliation_id", None)
            next_lifecycle["correlation_envelope"].update(
                {
                    "event_id": next_event_id,
                    "causation_event_id": first_reconciliation["event_id"],
                    "producer": "execution.paper_runtime",
                    "event_time": "2026-07-15T10:00:08Z",
                    "received_at": "2026-07-15T10:00:08Z",
                }
            )
            next_lifecycle["metadata"].update(
                {
                    "correlation_envelope": next_lifecycle[
                        "correlation_envelope"
                    ],
                    "sequence_no": next_lifecycle["sequence_no"],
                    "causal_parent_id": first_reconciliation["event_id"],
                }
            )
            advanced_summary = json.loads(json.dumps(caught_up_summary))
            advanced_summary["last_event_id"] = next_event_id
            advanced_summary["last_lifecycle_identity"] = next_lifecycle
            fetch_summaries.return_value = [advanced_summary]

            advanced = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-visibility-003"},
            )

        assert advanced.json()["status"] == "ok"
        assert append.call_count == 2
        next_reconciliation = append.call_args_list[1].args[1]
        assert next_reconciliation["sequence_no"] == 9
        assert next_reconciliation["causal_parent_id"] == next_event_id


def test_scheduled_reconcile_accepts_receipted_reconciliation_before_new_aggregate() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        first_summary = _paper_lifecycle_summary()
        with (
            mock.patch.dict(
                "os.environ",
                {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"},
            ),
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[first_summary],
            ) as fetch_summaries,
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                return_value=_accepted_delivery(),
            ) as append,
        ):
            first = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-new-aggregate-001"},
            )
            assert first.json()["status"] == "ok"
            first_reconciliation = json.loads(json.dumps(append.call_args.args[1]))

            # The scheduler never observes the reconciliation as the latest
            # identity: a producer starts the next journey first. The bounded
            # receipt history is authoritative evidence that ingest/projector
            # did observe the accepted reconciliation in between.
            next_signal = json.loads(json.dumps(first_reconciliation))
            next_signal_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            next_trace_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            next_aggregate_id = "tj-paper-lifecycle-002"
            next_signal.update(
                {
                    "event_id": next_signal_id,
                    "event_type": "signal_generation",
                    "created_at": "2026-07-15T23:59:59Z",
                    "trace_id": next_trace_id,
                    "signal_id": "signal-paper-lifecycle-002",
                    "run_id": "run-paper-lifecycle-002",
                    "loop_run_id": "lr-paper-lifecycle-002",
                    "aggregate_id": next_aggregate_id,
                    "journey_id": next_aggregate_id,
                    "sequence_no": 1,
                    "causal_parent_id": "loop-trigger-paper-lifecycle-002",
                }
            )
            next_signal.pop("reconciliation_id", None)
            next_signal["correlation_envelope"].update(
                {
                    "journey_id": next_aggregate_id,
                    "correlation_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    "trace_id": next_trace_id,
                    "event_id": next_signal_id,
                    "causation_event_id": "loop-trigger-paper-lifecycle-002",
                    "producer": "strategy.signal_generator",
                    "event_time": "2026-07-15T23:59:59Z",
                    "received_at": "2026-07-15T23:59:59Z",
                }
            )
            next_signal["metadata"].update(
                {
                    "signal_id": next_signal["signal_id"],
                    "run_id": next_signal["run_id"],
                    "loop_run_id": next_signal["loop_run_id"],
                    "journey_id": next_aggregate_id,
                    "correlation_envelope": next_signal["correlation_envelope"],
                    "sequence_no": 1,
                    "causal_parent_id": next_signal["causal_parent_id"],
                }
            )
            next_summary = json.loads(json.dumps(first_summary))
            next_summary["last_event_id"] = next_signal_id
            next_summary["last_lifecycle_identity"] = next_signal
            next_summary["recent_lifecycle_event_ids"] = [
                first_summary["last_lifecycle_identity"]["event_id"],
                first_reconciliation["event_id"],
                next_signal_id,
            ]
            fetch_summaries.return_value = [next_summary]

            next_tick = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-new-aggregate-002"},
            )

        assert next_tick.json()["status"] == "ok"
        assert next_tick.json()["lifecycle_retryable_errors"] == []
        assert append.call_count == 2
        next_reconciliation = append.call_args_list[1].args[1]
        assert next_reconciliation["aggregate_id"] == next_aggregate_id
        assert next_reconciliation["sequence_no"] == 2
        assert next_reconciliation["causal_parent_id"] == next_signal_id
        first_evaluation = svc.store.get_evaluation(
            svc._tick_evaluation_id(
                "tick-new-aggregate-001", first_summary["binding_id"]
            )
        )
        assert (
            first_evaluation["lifecycle_append"]["summary_visibility_source"]
            == "recent_lifecycle_event_ids"
        )


def test_latest_accepted_append_uses_delivery_time_across_aggregate_sequences() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        binding_id = "binding-accepted-order-001"
        svc.store.put_evaluation(
            {
                "evaluation_id": "evaluation-high-sequence",
                "binding_id": binding_id,
                "evaluated_at": "2026-07-15T10:00:00Z",
                "lifecycle_append": {
                    "status": "accepted",
                    "accepted_at": "2026-07-15T10:00:01Z",
                    "event_id": "event-high-sequence",
                    "event": {
                        "event_id": "event-high-sequence",
                        "aggregate_type": "trade_journey",
                        "aggregate_id": "journey-high-sequence",
                        "sequence_no": 100,
                    },
                },
            }
        )
        svc.store.put_evaluation(
            {
                "evaluation_id": "evaluation-new-journey",
                "binding_id": binding_id,
                "evaluated_at": "2026-07-15T10:05:00Z",
                "lifecycle_append": {
                    "status": "accepted",
                    "accepted_at": "2026-07-15T10:05:01Z",
                    "event_id": "event-new-journey",
                    "event": {
                        "event_id": "event-new-journey",
                        "aggregate_type": "trade_journey",
                        "aggregate_id": "journey-new",
                        "sequence_no": 2,
                    },
                },
            }
        )

        _, state = svc._latest_accepted_lifecycle_append(binding_id)

        assert state["event_id"] == "event-new-journey"


def test_cross_aggregate_visibility_requires_ordered_projector_receipts() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        binding_id = "binding-receipt-order-001"
        svc.store.put_evaluation(
            {
                "evaluation_id": "evaluation-receipt-order",
                "binding_id": binding_id,
                "evaluated_at": "2026-07-15T10:00:00Z",
                "lifecycle_append": {
                    "status": "accepted",
                    "accepted_at": "2026-07-15T10:00:01Z",
                    "summary_visibility_confirmed_at": "2026-07-15T10:00:02Z",
                    "event_id": "accepted-receipt-order",
                    "event": {
                        "event_id": "accepted-receipt-order",
                        "created_at": "2026-07-15T10:00:00Z",
                        "aggregate_type": "trade_journey",
                        "aggregate_id": "journey-receipt-order-old",
                        "sequence_no": 100,
                    },
                },
            }
        )
        summary = {
            "last_lifecycle_identity": {
                "event_id": "newer-timestamp-without-receipt",
                "created_at": "2026-07-15T11:00:00Z",
                "aggregate_type": "trade_journey",
                "aggregate_id": "journey-receipt-order-new",
                "sequence_no": 1,
            },
            "recent_lifecycle_event_ids": ["newer-timestamp-without-receipt"],
        }

        reason, _ = svc._accepted_append_visibility_reason(
            summary=summary,
            binding_id=binding_id,
            timestamp="2026-07-15T11:00:01Z",
        )

        assert reason == "accepted_lifecycle_append_not_visible"


def test_same_aggregate_visibility_rejects_reversed_projector_receipts() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        binding_id = "binding-reversed-receipts-001"
        svc.store.put_evaluation(
            {
                "evaluation_id": "evaluation-reversed-receipts",
                "binding_id": binding_id,
                "evaluated_at": "2026-07-15T10:00:00Z",
                "lifecycle_append": {
                    "status": "accepted",
                    "accepted_at": "2026-07-15T10:00:01Z",
                    "event_id": "accepted-reversed-receipts",
                    "event": {
                        "event_id": "accepted-reversed-receipts",
                        "aggregate_type": "trade_journey",
                        "aggregate_id": "journey-reversed-receipts",
                        "sequence_no": 7,
                    },
                },
            }
        )
        summary = {
            "last_lifecycle_identity": {
                "event_id": "observed-before-accepted-receipt",
                "aggregate_type": "trade_journey",
                "aggregate_id": "journey-reversed-receipts",
                "sequence_no": 8,
            },
            "recent_lifecycle_event_ids": [
                "observed-before-accepted-receipt",
                "accepted-reversed-receipts",
            ],
        }

        reason, _ = svc._accepted_append_visibility_reason(
            summary=summary,
            binding_id=binding_id,
            timestamp="2026-07-15T10:00:02Z",
        )

        assert reason == "accepted_lifecycle_append_not_visible"
        accepted_evaluation = svc.store.get_evaluation(
            "evaluation-reversed-receipts"
        )
        assert "summary_visibility_confirmed_at" not in accepted_evaluation[
            "lifecycle_append"
        ]


def test_scheduled_reconcile_emits_failed_event_for_non_ok_evaluation() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _paper_lifecycle_summary(queue_lag_ms=20_000)
        with (
            mock.patch.dict(
                "os.environ",
                {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"},
            ),
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[summary],
            ),
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                return_value=_accepted_delivery(),
            ) as append,
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-paper-failed-001"},
            )

        assert response.json()["status"] == "critical"
        event = append.call_args.args[1]
        assert event["event_type"] == "reconciliation_failed"
        assert event["metrics"]["reconciliation_status"] == "critical"


def test_scheduled_reconcile_never_appends_incomplete_identity_or_live_capital() -> None:
    from fastapi.testclient import TestClient

    missing = _healthy_runtime_summary(binding_id="rtb-missing-lifecycle-001")
    live = _paper_lifecycle_summary()
    live["binding_id"] = "55555555-5555-4555-8555-555555555555"
    live["deployment_stage"] = "live"
    live_identity = json.loads(json.dumps(live["last_lifecycle_identity"]))
    live_identity.update(
        binding_id=live["binding_id"],
        environment="live",
        execution_mode="live",
        deployment_stage="live",
    )
    live_identity["correlation_envelope"]["environment"] = "live"
    live["last_lifecycle_identity"] = live_identity
    incomplete = _paper_lifecycle_summary()
    incomplete["binding_id"] = "66666666-6666-4666-8666-666666666666"
    incomplete_identity = json.loads(json.dumps(incomplete["last_lifecycle_identity"]))
    incomplete_identity["binding_id"] = incomplete["binding_id"]
    incomplete_identity.pop("run_id")
    incomplete["last_lifecycle_identity"] = incomplete_identity
    missing_persona = _paper_lifecycle_summary()
    missing_persona["binding_id"] = "77777777-7777-4777-8777-777777777777"
    missing_persona_identity = json.loads(
        json.dumps(missing_persona["last_lifecycle_identity"])
    )
    missing_persona_identity["binding_id"] = missing_persona["binding_id"]
    missing_persona_identity["authority_refs"].pop("persona_id")
    missing_persona_identity["metadata"].pop("persona_id", None)
    missing_persona["last_lifecycle_identity"] = missing_persona_identity

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        with (
            mock.patch.dict(
                "os.environ",
                {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"},
            ),
            mock.patch.object(
                svc,
                "_fetch_telemetry_runtime_summaries",
                return_value=[missing, live, incomplete, missing_persona],
            ),
            mock.patch.object(svc, "_append_telemetry_lifecycle_event") as append,
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-ineligible-lifecycle-001"},
            )

        assert response.status_code == 201
        assert append.call_count == 0
        results = {
            item["binding_id"]: item for item in response.json()["lifecycle_append_results"]
        }
        assert results[missing["binding_id"]]["status"] == "not_eligible"
        assert results[missing["binding_id"]]["reason"] == "missing_lifecycle_identity"
        assert results[live["binding_id"]]["status"] == "not_eligible"
        assert results[live["binding_id"]]["reason"] == "non_paper_lifecycle"
        assert results[incomplete["binding_id"]]["status"] == "not_eligible"
        assert results[incomplete["binding_id"]]["reason"].startswith(
            "incomplete_lifecycle_identity:run_id"
        )
        assert results[missing_persona["binding_id"]]["status"] == "not_eligible"
        assert (
            results[missing_persona["binding_id"]]["reason"]
            == "missing_lifecycle_persona_id"
        )


def test_telemetry_lifecycle_delivery_classifies_terminal_and_retryable_outcomes() -> None:
    class AcceptedResponse:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"status":"accepted"}'

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        event = {"event_id": "reconciliation-test-event"}
        with mock.patch.object(svc.urllib.request, "urlopen", return_value=AcceptedResponse()):
            accepted = svc._append_telemetry_lifecycle_event("http://telemetry:8083", event)
        assert accepted["status"] == "accepted"
        assert accepted["terminal"] is True
        assert accepted["retryable"] is False

        with mock.patch.object(
            svc.urllib.request,
            "urlopen",
            side_effect=svc.urllib.error.URLError("connection reset"),
        ):
            ambiguous = svc._append_telemetry_lifecycle_event(
                "http://telemetry:8083", event
            )
        assert ambiguous["status"] == "retryable_error"
        assert ambiguous["outcome"] == "ambiguous"
        assert ambiguous["terminal"] is False
        assert ambiguous["retryable"] is True

        rejected_error = svc.urllib.error.HTTPError(
            "http://telemetry:8083/api/telemetry/ingest",
            400,
            "bad request",
            {},
            io.BytesIO(b'{"status":"rejected"}'),
        )
        with mock.patch.object(
            svc.urllib.request,
            "urlopen",
            side_effect=rejected_error,
        ):
            rejected = svc._append_telemetry_lifecycle_event(
                "http://telemetry:8083", event
            )
        assert rejected["status"] == "terminal_rejected"
        assert rejected["terminal"] is True
        assert rejected["retryable"] is False

        unavailable_error = svc.urllib.error.HTTPError(
            "http://telemetry:8083/api/telemetry/ingest",
            503,
            "service unavailable",
            {},
            io.BytesIO(b'{"status":"unavailable"}'),
        )
        with mock.patch.object(
            svc.urllib.request,
            "urlopen",
            side_effect=unavailable_error,
        ):
            unavailable = svc._append_telemetry_lifecycle_event(
                "http://telemetry:8083", event
            )
        assert unavailable["status"] == "retryable_error"
        assert unavailable["outcome"] == "failed"
        assert unavailable["terminal"] is False
        assert unavailable["retryable"] is True
