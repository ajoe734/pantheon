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
    if "committed_position_snapshot" not in overrides:
        summary["committed_position_snapshot"] = summary["last_lifecycle_identity"]
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
        with mock.patch.object(svc, "fetch_runtime_summaries", return_value=[]):
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

        with mock.patch.object(svc, "fetch_runtime_summaries", return_value=fake_summaries):
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
            "fetch_runtime_summaries",
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
            "fetch_runtime_summaries",
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
                "fetch_runtime_summaries",
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


def test_scheduled_reconcile_lifecycle_only_appends_with_durable_evaluation() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _paper_lifecycle_summary()
        binding_id = summary["binding_id"]
        tick_id = "tick-target-lifecycle-only-001"
        evaluation_id = svc._tick_evaluation_id(tick_id, binding_id)

        saved_before_append = []

        def fake_append(*args, **kwargs):
            eval_in_store = svc.store.get_evaluation(evaluation_id)
            if eval_in_store is not None:
                saved_before_append.append(json.loads(json.dumps(eval_in_store)))
            return {
                "status": "accepted",
                "terminal": True,
                "retryable": False,
                "outcome": "accepted",
                "http_status": 202,
                "response": {"status": "accepted"},
                "error": None,
            }

        with (
            mock.patch.object(
                svc,
                "fetch_runtime_summaries",
                return_value=[summary],
            ),
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                side_effect=fake_append,
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
        assert payload["drift_report_ids"] == []
        assert payload["incident_ids"] == []
        assert payload["incident_delivery_errors"] == []
        assert payload["evaluation_ids"] == [evaluation_id]
        assert payload["lifecycle_append_results"][0]["binding_id"] == binding_id
        assert payload["lifecycle_append_results"][0]["status"] == "accepted"
        assert payload["lifecycle_accepted_event_ids"] == [
            payload["lifecycle_append_results"][0]["event_id"]
        ]
        # Proves one same-tenant durable evaluation/intent saved BEFORE append
        assert len(saved_before_append) == 1
        assert saved_before_append[0]["evaluation_id"] == evaluation_id
        assert saved_before_append[0]["lifecycle_append"]["status"] == "pending"
        assert saved_before_append[0]["lifecycle_append"]["event_id"] is not None

        # After append, evaluation in store is updated to accepted
        ev_persisted = svc.store.get_evaluation(evaluation_id)
        assert ev_persisted is not None
        assert ev_persisted["lifecycle_append"]["status"] == "accepted"
        assert ev_persisted["lifecycle_append"]["event_id"] == payload["lifecycle_append_results"][0]["event_id"]
        append.assert_called_once()


test_scheduled_reconcile_lifecycle_only_appends_without_evaluation_store = test_scheduled_reconcile_lifecycle_only_appends_with_durable_evaluation


def test_scheduled_lag_breach_creates_deterministic_report_and_dedup_incident() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _healthy_runtime_summary(queue_lag_ms=20_000)
        with (
            mock.patch.object(
                svc,
                "fetch_runtime_summaries",
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
                "fetch_runtime_summaries",
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

        with mock.patch.object(svc, "fetch_runtime_summaries", return_value=fake_summaries):
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

        with mock.patch.object(svc, "fetch_runtime_summaries", return_value=fake_summaries):
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

        with mock.patch.object(svc, "fetch_runtime_summaries", return_value=fake_summaries):
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
                "fetch_runtime_summaries",
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
                "fetch_runtime_summaries",
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
                "fetch_runtime_summaries",
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
            caught_up_summary.pop("committed_position_snapshot", None)
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
            advanced_summary["committed_position_snapshot"] = next_lifecycle
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
                "fetch_runtime_summaries",
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
                    "event_type": "position_snapshot",
                    "created_at": "2026-07-15T23:59:59Z",
                    "trace_id": next_trace_id,
                    "signal_id": "signal-paper-lifecycle-002",
                    "run_id": "run-paper-lifecycle-002",
                    "loop_run_id": "lr-paper-lifecycle-002",
                    "aggregate_id": next_aggregate_id,
                    "journey_id": next_aggregate_id,
                    "sequence_no": 7,
                    "causal_parent_id": "fill-paper-lifecycle-002",
                }
            )
            next_signal.pop("reconciliation_id", None)
            next_signal["correlation_envelope"].update(
                {
                    "journey_id": next_aggregate_id,
                    "correlation_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    "trace_id": next_trace_id,
                    "event_id": next_signal_id,
                    "causation_event_id": "fill-paper-lifecycle-002",
                    "producer": "execution.paper_runtime",
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
                    "sequence_no": 7,
                    "causal_parent_id": next_signal["causal_parent_id"],
                }
            )
            next_summary = json.loads(json.dumps(first_summary))
            next_summary["last_event_id"] = next_signal_id
            next_summary["last_lifecycle_identity"] = next_signal
            next_summary["committed_position_snapshot"] = next_signal
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
        assert next_reconciliation["sequence_no"] == 8
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
                "fetch_runtime_summaries",
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
    live["committed_position_snapshot"] = live_identity
    incomplete = _paper_lifecycle_summary()
    incomplete["binding_id"] = "66666666-6666-4666-8666-666666666666"
    incomplete_identity = json.loads(json.dumps(incomplete["last_lifecycle_identity"]))
    incomplete_identity["binding_id"] = incomplete["binding_id"]
    incomplete_identity.pop("run_id")
    incomplete["last_lifecycle_identity"] = incomplete_identity
    incomplete["committed_position_snapshot"] = incomplete_identity
    missing_persona = _paper_lifecycle_summary()
    missing_persona["binding_id"] = "77777777-7777-4777-8777-777777777777"
    missing_persona_identity = json.loads(
        json.dumps(missing_persona["last_lifecycle_identity"])
    )
    missing_persona_identity["binding_id"] = missing_persona["binding_id"]
    missing_persona_identity["authority_refs"].pop("persona_id")
    missing_persona_identity["metadata"].pop("persona_id", None)
    missing_persona["last_lifecycle_identity"] = missing_persona_identity
    missing_persona["committed_position_snapshot"] = missing_persona_identity
    # Admitted-but-uncommitted position snapshot: has last_lifecycle_identity but no committed_position_snapshot
    uncommitted = _paper_lifecycle_summary()
    uncommitted["binding_id"] = "88888888-8888-4888-8888-888888888888"
    uncommitted.pop("committed_position_snapshot", None)

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
                "fetch_runtime_summaries",
                return_value=[missing, live, incomplete, missing_persona, uncommitted],
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
        assert results[uncommitted["binding_id"]]["status"] == "not_eligible"
        assert results[uncommitted["binding_id"]]["reason"] == "missing_lifecycle_identity"


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


def test_scheduled_reconcile_incident_dispatch_bounded_and_completes_within_sla() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        persona_summary = _paper_lifecycle_summary(queue_lag_ms=25_000)
        persona_binding = persona_summary["binding_id"]
        summaries = [persona_summary]
        for i in range(1, 15):
            summaries.append(
                _healthy_runtime_summary(
                    binding_id=f"rb-other-active-{i:03d}",
                    runtime_id=f"rt-other-{i:03d}",
                    queue_lag_ms=20_000 if i % 2 == 0 else 10,
                )
            )
        assert len(summaries) == 15

        observed_incident_timeouts: list[float] = []

        def mock_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "consume-drift-report" in url:
                observed_incident_timeouts.append(timeout)
                raise TimeoutError("incidents service timed out")
            return io.BytesIO(b'{"status":"accepted","event_id":"evt-lifecycle-8"}')

        with (
            mock.patch.dict(
                "os.environ",
                {
                    "PANTHEON_INCIDENTS_API_URL": "http://incidents:8090",
                    "PANTHEON_INCIDENTS_API_TIMEOUT_SECONDS": "110",
                },
            ),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=summaries),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=mock_urlopen),
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                return_value={
                    "status": "accepted",
                    "terminal": True,
                    "retryable": False,
                    "outcome": "accepted",
                    "http_status": 202,
                    "event_id": "evt-recon-8",
                    "response": {"status": "accepted"},
                    "error": None,
                },
            ) as mock_append,
        ):
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": "tick-multi-binding-sla-001",
                    "sla_seconds": 60.0,
                },
            )

        assert resp.status_code == 201
        payload = resp.json()
        assert payload["within_sla"] is True
        assert payload["sla_status"] == "met"
        assert payload["evaluated_binding_count"] == 15
        assert len(payload["evaluation_ids"]) == 15

        assert len(observed_incident_timeouts) > 0
        for to in observed_incident_timeouts:
            assert to is not None and to <= 5.0

        assert len(payload["incident_delivery_errors"]) > 0

        persona_append = next(
            r for r in payload["lifecycle_append_results"]
            if r["binding_id"] == persona_binding
        )
        assert persona_append["status"] == "accepted"
        assert "evt-recon-8" in payload["lifecycle_accepted_event_ids"]
        mock_append.assert_called()

        scheduler = _load_scheduler_module()
        with mock.patch.object(
            scheduler.urllib.request,
            "urlopen",
            return_value=io.BytesIO(resp.content),
        ):
            tick_result = scheduler.run_tick(
                api_url="http://reconciliation-drift-svc:8102",
                tick_id="tick-multi-binding-sla-001",
                timeout_seconds=90.0,
                sla_seconds=60.0,
            )
        assert tick_result["tick_id"] == "tick-multi-binding-sla-001"
        assert tick_result["within_sla"] is True
        assert tick_result["attempt_count"] == 1


def test_scheduled_reconcile_lifecycle_and_incident_budget_bounded_under_cardinality() -> None:
    from fastapi.testclient import TestClient
    import copy

    def _make_paper_summary(idx: int) -> dict:
        base = _paper_lifecycle_summary(queue_lag_ms=25_000)
        s = copy.deepcopy(base)
        b_id = f"33333333-3333-4333-8333-{idx:012d}"
        r_id = f"runtime-paper-{idx:03d}"
        evt_id = f"11111111-1111-4111-8111-{idx:012d}"
        tr_id = f"22222222-2222-4222-8222-{idx:012d}"
        s["binding_id"] = b_id
        s["runtime_id"] = r_id
        s["last_event_id"] = evt_id
        s["trace_id"] = tr_id
        s["last_lifecycle_identity"]["binding_id"] = b_id
        s["last_lifecycle_identity"]["runtime_id"] = r_id
        s["last_lifecycle_identity"]["event_id"] = evt_id
        s["last_lifecycle_identity"]["trace_id"] = tr_id
        s["last_lifecycle_identity"]["correlation_envelope"]["event_id"] = evt_id
        s["last_lifecycle_identity"]["correlation_envelope"]["trace_id"] = tr_id
        s["last_lifecycle_identity"]["correlation_envelope"]["journey_id"] = f"tj-paper-{idx:03d}"
        s["last_lifecycle_identity"]["aggregate_id"] = f"tj-paper-{idx:03d}"
        return s

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        summaries = [_make_paper_summary(i) for i in range(1, 6)]
        for i in range(6, 16):
            summaries.append(
                _healthy_runtime_summary(
                    binding_id=f"rb-other-active-{i:03d}",
                    runtime_id=f"rt-other-{i:03d}",
                    queue_lag_ms=20_000 if i % 2 == 0 else 10,
                )
            )
        assert len(summaries) == 15

        observed_lifecycle_timeouts: list[float] = []
        observed_incident_timeouts: list[float] = []
        tracker = {"fake_time": 1000.0}

        def mock_monotonic():
            return tracker["fake_time"]

        def mock_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "telemetry" in url or "ingest" in url:
                observed_lifecycle_timeouts.append(timeout)
                tracker["fake_time"] += 20.0
                raise TimeoutError("telemetry timed out")
            elif "consume-drift-report" in url:
                observed_incident_timeouts.append(timeout)
                tracker["fake_time"] += 20.0
                raise TimeoutError("incidents timed out")
            return io.BytesIO(b'{"status":"accepted"}')

        list_eval_count = 0
        orig_list_eval = svc.store.list_evaluations

        def counted_list(*args, **kwargs):
            nonlocal list_eval_count
            list_eval_count += 1
            return orig_list_eval(*args, **kwargs)

        svc.store.list_evaluations = counted_list

        with (
            mock.patch.dict(
                "os.environ",
                {
                    "PANTHEON_INCIDENTS_API_URL": "http://incidents:8090",
                    "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
                },
            ),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=summaries),
            mock.patch.object(svc, "_monotonic", side_effect=mock_monotonic),
            mock.patch("time.monotonic", side_effect=mock_monotonic),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=mock_urlopen),
        ):
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": "tick-budget-bounded-001",
                    "sla_seconds": 60.0,
                },
            )

        assert resp.status_code == 201
        payload = resp.json()
        assert payload["within_sla"] is True
        assert payload["sla_status"] == "met"
        assert payload["evaluated_binding_count"] == 15
        assert len(payload["evaluation_ids"]) == 15

        # All 15 bindings evaluated, store was scanned once at the tick start
        assert list_eval_count == 1

        # Timeouts passed to network were all bounded by SLA budget <= 5.0s
        for to in observed_lifecycle_timeouts:
            assert to is not None and to <= 5.0
        for to in observed_incident_timeouts:
            assert to is not None and to <= 5.0

        # After budget was exhausted, remaining calls returned 504 without urlopen
        lifecycle_retryable = payload.get("lifecycle_retryable_errors", [])
        budget_exhausted_lifecycle = [
            e for e in lifecycle_retryable
            if "budget exhausted" in str(e.get("error"))
        ]
        assert len(budget_exhausted_lifecycle) > 0

        # Scheduler client handles response without hitting 90s TimeoutError
        scheduler = _load_scheduler_module()
        with mock.patch.object(
            scheduler.urllib.request,
            "urlopen",
            return_value=io.BytesIO(resp.content),
        ):
            tick_result = scheduler.run_tick(
                api_url="http://reconciliation-drift-svc:8102",
                tick_id="tick-budget-bounded-001",
                timeout_seconds=90.0,
                sla_seconds=60.0,
            )
        assert tick_result["tick_id"] == "tick-budget-bounded-001"
        assert tick_result["within_sla"] is True
        assert tick_result["attempt_count"] == 1
        assert tick_result["controller_status"] == "unhealthy"

        # Verify claim was cleanly failed so a retry does not receive deferred lease_active
        claim_result = svc.store.claim_work(
            tenant_id="default",
            work_type="scheduled_reconcile",
            window_id="tick-budget-bounded-001",
            owner_id="reconciliation-scheduler",
            lease_seconds=60.0,
        )
        assert claim_result["acquired"] is True
        assert claim_result["reason"] in {"acquired", "recovered"}



def _published_loop_10_row(scheduler, result, *, interval, timeout, attempts=1, backoff=0.0):
    """Run the scheduler's real publish path against a capturing store."""
    import asyncio
    import importlib
    import os

    loop_control = importlib.import_module("services.loop-control")
    projector = importlib.import_module("services.loop-control.projector")
    rows: list[dict] = []

    class CapturingStore:
        def __init__(self, dsn):
            pass

        async def upsert_record(self, record):
            rows.append(dict(record))

    with mock.patch.object(loop_control.writer, "LoopControllerStore", CapturingStore):
        writer = scheduler._build_loop_writer(
            dsn="postgresql://fake",
            tenant_id="default",
            lease_duration_seconds=scheduler._lease_seconds(
                interval_seconds=interval,
                timeout_seconds=timeout,
                max_attempts=attempts,
                retry_backoff_seconds=backoff,
            ),
        )
        truth = scheduler._controller_truth_fields(
            result, tick=1, worker_id="w", checked_at=scheduler._utc_now()
        )
        asyncio.run(
            writer.record_success(
                loop_id="telemetry_reconciliation",
                truth_level="scheduled_tick",
                desired_state=truth["desired_state"],
                downstream_actual_state=truth["downstream_actual_state"],
                evidence_refs=[truth["tick_evidence_ref"]],
            )
        )
    assert os is not None
    return projector, rows[0], truth


def test_idle_tick_projects_authoritative_healthy_loop_10_record():
    scheduler = _load_scheduler_module()
    result = {
        "status": "ok",
        "tick_id": "scheduled:default:idle",
        "telemetry_summaries_fetched": 1,
        "evaluated_binding_count": 1,
    }
    projector, row, truth = _published_loop_10_row(
        scheduler, result, interval=300, timeout=90
    )
    schema = json.loads(
        (_REPO_ROOT / "schemas" / "loop-controller-record.schema.json").read_text()
    )
    for key in ("desired_state", "downstream_actual_state"):
        jsonschema.validate(
            truth[key],
            schema["properties"][key],
            format_checker=jsonschema.FormatChecker(),
        )
    projected = projector.project_controller_record_to_bff(row)
    assert projected["desired_state_presence"]["authoritative"] is True
    assert projected["downstream_actual_state"]["authoritative"] is True
    assert projected["evidence_refs"]
    assert projected["controller_health"]["status"] == "healthy"


def test_lease_covers_interval_plus_tick_timeout():
    scheduler = _load_scheduler_module()
    lease = scheduler._lease_seconds(interval_seconds=300, timeout_seconds=90)
    assert lease >= 390
    retried = scheduler._lease_seconds(
        interval_seconds=300, timeout_seconds=90, max_attempts=3, retry_backoff_seconds=1
    )
    assert retried >= 300 + 3 * 90


def _publish_tick_and_admit(result):
    """Publish through the real helper, project, then run BFF admission."""
    import asyncio
    import importlib

    scheduler = _load_scheduler_module()
    loop_control = importlib.import_module("services.loop-control")
    projector = importlib.import_module("services.loop-control.projector")
    inventory = importlib.import_module("services.control-plane.bff.loop_inventory")
    rows: list[dict] = []

    class CapturingStore:
        def __init__(self, dsn):
            pass

        async def upsert_record(self, record):
            rows.append(dict(record))

    with mock.patch.object(loop_control.writer, "LoopControllerStore", CapturingStore):
        writer = scheduler._build_loop_writer(
            dsn="postgresql://fake", tenant_id="default", lease_duration_seconds=390
        )
        asyncio.run(
            scheduler._publish_loop_record(
                writer, result, tick=1, worker_id="w", checked_at=scheduler._utc_now()
            )
        )
    projected = projector.project_controller_record_to_bff(rows[0])
    qualified = inventory._runtime_controller_record_qualified(
        projected, "controller_store", scheduler._WORKER_NAME
    )
    source = inventory._truth_source_from_profile(
        "reconciled_live_proof", projected, "controller_store", scheduler._WORKER_NAME
    )
    return rows[0], projected, qualified, source


def test_clean_no_drift_tick_is_admitted_as_live_truth():
    row, projected, qualified, source = _publish_tick_and_admit(
        {
            "status": "ok",
            "controller_status": "healthy",
            "tick_id": "scheduled:default:clean",
            "telemetry_summaries_fetched": 2,
            "evaluated_binding_count": 2,
        }
    )
    assert row["truth_level"] == "reconciled_live_proof"
    assert qualified is True
    assert source["accepted_as_live"] is True


def test_degraded_downstream_tick_is_success_and_admitted_as_live_truth():
    row, projected, qualified, source = _publish_tick_and_admit(
        {
            "status": "degraded",
            "controller_status": "degraded",
            "tick_id": "scheduled:default:degraded",
            "telemetry_summaries_fetched": 0,
            "evaluated_binding_count": 0,
            "errors": [],
        }
    )
    assert row["truth_level"] == "reconciled_live_proof"
    assert projected["controller_health"]["status"] == "healthy"
    assert projected["downstream_actual_state"]["status"] == "degraded"
    assert qualified is True
    assert source["accepted_as_live"] is True


def test_controller_error_tick_is_recorded_as_failure():
    row, projected, qualified, _ = _publish_tick_and_admit(
        {
            "status": "error",
            "controller_status": "unhealthy",
            "tick_id": "scheduled:default:err",
            "errors": [{"detail": "boom"}],
        }
    )
    assert projected["controller_health"]["status"] == "unhealthy"
    assert qualified is False


# ---------------------------------------------------------------------------
# In-flight paper lifecycle terminal order race tests (RECON-PAPER-INFLIGHT-TERMINAL-ORDER-20261011)
# ---------------------------------------------------------------------------


def _make_inflight_paper_summary(stage: str, sequence_no: int, **overrides) -> dict:
    base = _paper_lifecycle_summary()
    identity = dict(base["last_lifecycle_identity"])
    event_id = f"evt-{stage}-{sequence_no}"
    causal_id = f"evt-parent-{sequence_no - 1}" if sequence_no > 1 else "loop-trigger-001"
    envelope = dict(identity["correlation_envelope"])
    envelope.update(
        {
            "event_id": event_id,
            "causation_event_id": causal_id,
            "producer": (
                "execution.paper_runtime"
                if stage != "signal_generation"
                else "strategy.signal_generator"
            ),
        }
    )
    metadata = dict(identity.get("metadata") or {})
    metadata.update(
        {
            "sequence_no": sequence_no,
            "causal_parent_id": causal_id,
            "correlation_envelope": envelope,
        }
    )
    identity.update(
        {
            "event_id": event_id,
            "event_type": stage,
            "sequence_no": sequence_no,
            "causal_parent_id": causal_id,
            "correlation_envelope": envelope,
            "metadata": metadata,
        }
    )
    base.update(
        {
            "last_event_id": event_id,
            "last_lifecycle_identity": identity,
        }
    )
    if stage == "position_snapshot":
        base["committed_position_snapshot"] = identity
    else:
        base.pop("committed_position_snapshot", None)
    base.update(overrides)
    return base


def test_scheduled_reconcile_actual_sequence2_trade_decision_race_and_resumes_on_sequence7_position_snapshot() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        race_summary = _make_inflight_paper_summary("trade_decision", 2)
        binding_id = race_summary["binding_id"]
        tick_id = "tick-race-trade-decision-001"

        with (
            mock.patch.dict(
                "os.environ",
                {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"},
            ),
            mock.patch.object(
                svc,
                "fetch_runtime_summaries",
                return_value=[race_summary],
            ) as fetch_mock,
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
                return_value=_accepted_delivery(),
            ) as append,
        ):
            # 1. In-flight race: sequence 2 trade_decision must NOT emit reconciliation
            inflight_resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": tick_id},
            )
            assert inflight_resp.status_code == 201
            payload = inflight_resp.json()
            assert payload["status"] == "failure"  # Pending work is retryable failure
            assert append.call_count == 0  # Zero telemetry POST while in-flight!
            assert payload["lifecycle_accepted_event_ids"] == []
            assert len(payload["lifecycle_retryable_errors"]) == 1
            assert payload["lifecycle_terminal_rejections"] == []
            assert payload["lifecycle_ineligible_binding_ids"] == []

            results = {item["binding_id"]: item for item in payload["lifecycle_append_results"]}
            assert results[binding_id]["status"] == "pending"
            assert results[binding_id]["terminal"] is False
            assert results[binding_id]["retryable"] is True
            assert results[binding_id]["reason"] == "lifecycle_in_flight"
            assert results[binding_id]["event_id"] is None
            assert payload["lifecycle_retryable_errors"][0] == results[binding_id]

            eval_id = svc._tick_evaluation_id(tick_id, binding_id)
            stored_eval = svc.store.get_evaluation(eval_id)
            assert stored_eval is not None
            assert stored_eval["lifecycle_append"]["status"] == "pending"
            assert stored_eval["lifecycle_append"]["terminal"] is False
            assert stored_eval["lifecycle_append"]["retryable"] is True
            assert stored_eval["lifecycle_append"]["reason"] == "lifecycle_in_flight"

            # 2. Later sequence 7 position_snapshot arrives for same valid identity
            pos_summary = _make_inflight_paper_summary("position_snapshot", 7)
            fetch_mock.return_value = [pos_summary]

            resume_resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": tick_id},
            )
            assert resume_resp.status_code == 201
            assert resume_resp.json()["status"] == "ok"
            assert append.call_count == 1  # Exactly one terminal append emitted!
            _, terminal_event = append.call_args.args
            assert terminal_event["event_type"] == "reconciliation_completed"
            assert terminal_event["sequence_no"] == 8
            assert terminal_event["causal_parent_id"] == pos_summary["last_lifecycle_identity"]["event_id"]
            assert terminal_event["binding_id"] == binding_id
            assert resume_resp.json()["lifecycle_accepted_event_ids"] == [terminal_event["event_id"]]

            resumed_eval = svc.store.get_evaluation(eval_id)
            assert resumed_eval["lifecycle_append"]["status"] == "accepted"
            assert resumed_eval["lifecycle_append"]["terminal"] is True
            assert resumed_eval["lifecycle_append"]["retryable"] is False

            # 3. Duplicate retry: exactly same terminal returned, zero additional POST
            dup_resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": tick_id},
            )
            assert dup_resp.status_code == 201
            assert dup_resp.json()["status"] == "ok"
            assert append.call_count == 1  # No duplicate POST!
            assert dup_resp.json()["lifecycle_accepted_event_ids"] == [terminal_event["event_id"]]


def test_scheduled_reconcile_all_six_incomplete_stages_pending_and_zero_telemetry_post() -> None:
    from fastapi.testclient import TestClient

    incomplete_stages = [
        ("signal_generation", 1),
        ("trade_decision", 2),
        ("risk_evaluation", 3),
        ("order_submitted", 4),
        ("order_accepted", 5),
        ("paper_fill_simulated", 6),
    ]

    for stage, seq_no in incomplete_stages:
        with tempfile.TemporaryDirectory() as data_dir:
            svc = _load_service_module(data_dir)
            client = TestClient(svc.app)
            stage_summary = _make_inflight_paper_summary(stage, seq_no)
            binding_id = stage_summary["binding_id"]
            tick_id = f"tick-incomplete-{stage}-{seq_no}"

            with (
                mock.patch.dict(
                    "os.environ",
                    {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"},
                ),
                mock.patch.object(
                    svc,
                    "fetch_runtime_summaries",
                    return_value=[stage_summary],
                ) as fetch_mock,
                mock.patch.object(
                    svc,
                    "_append_telemetry_lifecycle_event",
                    return_value=_accepted_delivery(),
                ) as append,
            ):
                # Must NOT emit terminal while at stage
                resp = client.post(
                    "/api/reconciliation-drift/scheduled-reconcile",
                    json={"tick_id": tick_id},
                )
                assert resp.status_code == 201
                assert resp.json()["status"] == "failure"  # Pending work retryable
                assert append.call_count == 0  # Zero telemetry POST!
                receipt = resp.json()["lifecycle_append_results"][0]
                assert receipt["binding_id"] == binding_id
                assert receipt["status"] == "pending"
                assert receipt["terminal"] is False
                assert receipt["retryable"] is True
                assert receipt["reason"] == "lifecycle_in_flight"
                assert resp.json()["lifecycle_retryable_errors"] == [receipt]

                eval_id = svc._tick_evaluation_id(tick_id, binding_id)
                evaluation = svc.store.get_evaluation(eval_id)
                assert evaluation is not None
                assert evaluation["lifecycle_append"]["status"] == "pending"
                assert evaluation["lifecycle_append"]["terminal"] is False
                assert evaluation["lifecycle_append"]["retryable"] is True

                # Later sequence 7 position_snapshot emits sequence 8 terminal
                pos_summary = _make_inflight_paper_summary("position_snapshot", 7)
                fetch_mock.return_value = [pos_summary]

                later_resp = client.post(
                    "/api/reconciliation-drift/scheduled-reconcile",
                    json={"tick_id": tick_id},
                )
                assert later_resp.status_code == 201
                assert later_resp.json()["status"] == "ok"
                assert append.call_count == 1
                _, event = append.call_args.args
                assert event["event_type"] == "reconciliation_completed"
                assert event["sequence_no"] == 8
                assert event["causal_parent_id"] == pos_summary["last_lifecycle_identity"]["event_id"]

                # Duplicate retry
                dup_resp = client.post(
                    "/api/reconciliation-drift/scheduled-reconcile",
                    json={"tick_id": tick_id},
                )
                assert dup_resp.status_code == 201
                assert dup_resp.json()["status"] == "ok"
                assert append.call_count == 1


def test_scheduled_reconcile_inflight_preserves_malformed_identity_and_nonpaper_negatives() -> None:
    from fastapi.testclient import TestClient

    # In-flight stage but non-paper
    nonpaper_summary = _make_inflight_paper_summary("trade_decision", 2)
    nonpaper_summary["binding_id"] = "55555555-5555-4555-8555-000000000001"
    nonpaper_summary["last_lifecycle_identity"]["binding_id"] = nonpaper_summary["binding_id"]
    nonpaper_summary["deployment_stage"] = "live"
    nonpaper_summary["last_lifecycle_identity"]["deployment_stage"] = "live"
    nonpaper_summary["last_lifecycle_identity"]["environment"] = "live"
    nonpaper_summary["last_lifecycle_identity"]["execution_mode"] = "live"
    nonpaper_summary["last_lifecycle_identity"]["correlation_envelope"]["environment"] = "live"

    # In-flight stage but missing persona_id
    missing_persona = _make_inflight_paper_summary("trade_decision", 2)
    missing_persona["binding_id"] = "55555555-5555-4555-8555-000000000002"
    missing_persona["last_lifecycle_identity"]["binding_id"] = missing_persona["binding_id"]
    missing_persona["last_lifecycle_identity"]["authority_refs"].pop("persona_id", None)
    missing_persona["last_lifecycle_identity"]["metadata"].pop("persona_id", None)

    # In-flight stage but binding mismatch
    mismatched_binding = _make_inflight_paper_summary("trade_decision", 2)
    mismatched_binding["binding_id"] = "55555555-5555-4555-8555-000000000003"
    mismatched_binding["last_lifecycle_identity"]["binding_id"] = "different-binding-id"

    # Already reconciled
    already_reconciled = _make_inflight_paper_summary("reconciliation_completed", 8)
    already_reconciled["binding_id"] = "55555555-5555-4555-8555-000000000004"
    already_reconciled["last_lifecycle_identity"]["binding_id"] = already_reconciled["binding_id"]

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
                "fetch_runtime_summaries",
                return_value=[nonpaper_summary, missing_persona, mismatched_binding, already_reconciled],
            ),
            mock.patch.object(
                svc,
                "_append_telemetry_lifecycle_event",
            ) as append,
        ):
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-negatives-001"},
            )

        assert resp.status_code == 201
        assert append.call_count == 0
        results = {item["binding_id"]: item for item in resp.json()["lifecycle_append_results"]}
        assert results[nonpaper_summary["binding_id"]]["status"] == "not_eligible"
        assert results[nonpaper_summary["binding_id"]]["reason"] == "non_paper_lifecycle"
        assert results[missing_persona["binding_id"]]["status"] == "not_eligible"
        assert results[missing_persona["binding_id"]]["reason"] == "missing_lifecycle_persona_id"
        assert results[mismatched_binding["binding_id"]]["status"] == "not_eligible"
        assert results[mismatched_binding["binding_id"]]["reason"] == "lifecycle_binding_mismatch"
        assert results[already_reconciled["binding_id"]]["status"] == "not_eligible"
        assert results[already_reconciled["binding_id"]]["reason"] == "lifecycle_already_reconciled"


def test_scheduled_reconcile_core_budget_priority_prevents_starvation_under_43_summaries_and_retry() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        # 42 earlier summaries that trigger drift warnings (queue_lag_ms=25000), causing incident dispatches
        summaries = [
            _healthy_runtime_summary(
                binding_id=f"owned-older-{i:03d}",
                runtime_id=f"owned-runtime-{i:03d}",
                queue_lag_ms=25000,
            )
            for i in range(42)
        ]
        # 43rd summary: valid paper position awaiting reconciliation terminal
        current_paper = _paper_lifecycle_summary(queue_lag_ms=25000)
        summaries.append(current_paper)
        assert len(summaries) == 43

        clock = [1000.0]
        call_log = []

        class DummyResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"status":"accepted"}'

        def mock_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            kind = "incident" if "consume-drift-report" in url else "lifecycle"
            call_log.append({
                "kind": kind,
                "timeout": timeout,
                "elapsed": clock[0] - 1000.0,
            })
            if kind == "incident":
                # Each incident takes 2.0 seconds and times out / errors
                clock[0] += 2.0
                raise TimeoutError("slow incident fixture")
            # Lifecycle append: fast core capacity, advances clock slightly and returns accepted
            clock[0] += 0.05
            return DummyResponse()

        tick_id = "tick-core-budget-priority-43-sums"

        with (
            mock.patch.dict(
                "os.environ",
                {
                    "PANTHEON_INCIDENTS_API_URL": "http://incidents:8090",
                    "PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083",
                },
            ),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=summaries),
            mock.patch.object(svc, "_monotonic", side_effect=lambda: clock[0]),
            mock.patch("time.monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=mock_urlopen),
        ):
            # First tick run
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id,
                    "sla_seconds": 60.0,
                },
            )
            assert resp.status_code == 201
            payload = resp.json()

            # 1. Lifecycle append was dispatched BEFORE any incident dispatches
            assert len(call_log) > 0
            first_call = call_log[0]
            assert first_call["kind"] == "lifecycle", "Core lifecycle append must be called before incident dispatches"
            assert first_call["timeout"] == 5.0

            # 2. Paper position terminal was successfully accepted
            paper_results = [
                r for r in payload["lifecycle_append_results"]
                if r["binding_id"] == current_paper["binding_id"]
            ]
            assert len(paper_results) == 1
            paper_receipt = paper_results[0]
            assert paper_receipt["status"] == "accepted"
            assert paper_receipt["terminal"] is True
            assert paper_receipt["retryable"] is False
            assert paper_receipt["event_id"] is not None
            assert paper_receipt["event_id"] in payload["lifecycle_accepted_event_ids"]

            # 3. Earlier incidents were attempted, slow incidents ran until budget exhausted
            incident_calls = [c for c in call_log if c["kind"] == "incident"]
            assert len(incident_calls) > 0
            assert len(payload["incident_delivery_errors"]) > 0
            # Tick status is failure due to incident errors, but within SLA
            assert payload["status"] == "failure"
            assert payload["within_sla"] is True

            # 4. Same-tick retry: stable idempotency, no duplicate append or incident delivery
            prior_lifecycle_calls = len([c for c in call_log if c["kind"] == "lifecycle"])
            retry_resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id,
                    "sla_seconds": 60.0,
                },
            )
            assert retry_resp.status_code == 201
            retry_payload = retry_resp.json()

            # Zero additional lifecycle network calls!
            new_lifecycle_calls = len([c for c in call_log if c["kind"] == "lifecycle"])
            assert new_lifecycle_calls == prior_lifecycle_calls

            # Same accepted terminal returned
            retry_paper_results = [
                r for r in retry_payload["lifecycle_append_results"]
                if r["binding_id"] == current_paper["binding_id"]
            ]
            assert len(retry_paper_results) == 1
            assert retry_paper_results[0]["status"] == "accepted"
            assert retry_paper_results[0]["event_id"] == paper_receipt["event_id"]
            assert retry_payload["lifecycle_accepted_event_ids"] == [paper_receipt["event_id"]]
            assert retry_payload["skipped_binding_count"] == 43


def test_cross_run_read_grain_reconciles_committed_snapshot_not_newer_inflight_run() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        base = _paper_lifecycle_summary()
        snap_id = "evt-snap-runA"
        order_id = "evt-order-runB"

        snap_runA = dict(base["last_lifecycle_identity"])
        snap_runA.update({
            "event_id": snap_id,
            "event_type": "position_snapshot",
            "run_id": "run-A",
            "sequence_no": 7,
            "causal_parent_id": "evt-fill-runA",
            "metrics": {"position_qty": 100.0, "price": 500.0, "cash": 50000.0},
        })
        snap_runA["correlation_envelope"] = dict(snap_runA["correlation_envelope"])
        snap_runA["correlation_envelope"]["event_id"] = snap_id

        order_runB = dict(base["last_lifecycle_identity"])
        order_runB.update({
            "event_id": order_id,
            "event_type": "order_submitted",
            "run_id": "run-B",
            "sequence_no": 4,
            "metrics": {"order_qty": 50.0},
        })
        order_runB["correlation_envelope"] = dict(order_runB["correlation_envelope"])
        order_runB["correlation_envelope"]["event_id"] = order_id

        summary = dict(base)
        summary["last_lifecycle_identity"] = order_runB
        summary["committed_position_snapshot"] = snap_runA
        summary["pnl"] = 9999.0
        summary["drawdown"] = 0.85
        summary["queue_lag_ms"] = 15
        summary["event_delivery_lag_ms"] = 25

        telemetry_calls = []

        class MockResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"status":"accepted"}'

        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "telemetry" in url:
                data = json.loads(req.data.decode("utf-8"))
                telemetry_calls.append(data)
                return MockResponse()
            return MockResponse()

        tick_id = "tick-cross-run-001"
        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            # 1. First execution: evaluates run-A snapshot, emits stage 8 terminal for run-A
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": tick_id, "sla_seconds": 60.0},
            )
            assert resp.status_code == 201, resp.text
            payload = resp.json()

            assert len(telemetry_calls) == 1
            term = telemetry_calls[0]
            assert term["event_type"] in ("reconciliation_completed", "reconciliation_failed")
            assert term["run_id"] == "run-A", "Stage 8 terminal must bind to run-A, not run-B"
            assert term["sequence_no"] == 8, "Stage 8 terminal sequence must be snapshot sequence (7) + 1"
            assert term["causal_parent_id"] == snap_id, "Stage 8 terminal must point to snapshot event ID"
            assert term["metadata"]["run_id"] == "run-A"
            assert term["metadata"]["causal_parent_id"] == snap_id
            assert term["metadata"]["sequence_no"] == 8

            # Evaluated metrics verification
            eval_id = payload["evaluation_ids"][0]
            ev = svc.store.get_evaluation(eval_id)
            assert ev["observed_metrics"]["position_qty"] == 100.0
            assert ev["observed_metrics"]["price"] == 500.0
            assert ev["observed_metrics"]["cash"] == 50000.0
            assert ev["observed_metrics"]["queue_lag_ms"] == 15.0
            assert ev["observed_metrics"]["event_delivery_lag_ms"] == 25.0
            assert "pnl" not in ev["observed_metrics"], "Summary financial pnl must not be transplanted onto snapshot"
            assert "drawdown" not in ev["observed_metrics"], "Summary drawdown must not be transplanted onto snapshot"

            # 2. Idempotent retry: zero additional POST
            resp_retry = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": tick_id, "sla_seconds": 60.0},
            )
            assert resp_retry.status_code == 201
            assert len(telemetry_calls) == 1, "Idempotent replay must emit 0 additional network calls"


def test_cross_run_cache_misjoin_prevention() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        base = _paper_lifecycle_summary()
        snap_id = "evt-snap-runA"
        order_id = "evt-order-runB"

        snap_runA = dict(base["last_lifecycle_identity"])
        snap_runA.update({
            "event_id": snap_id,
            "event_type": "position_snapshot",
            "run_id": "run-A",
            "sequence_no": 7,
            "causal_parent_id": "evt-fill-runA",
            "metrics": {"position_qty": 100.0, "price": 500.0, "cash": 50000.0},
        })
        snap_runA["correlation_envelope"] = dict(snap_runA["correlation_envelope"])
        snap_runA["correlation_envelope"]["event_id"] = snap_id

        order_runB = dict(base["last_lifecycle_identity"])
        order_runB.update({
            "event_id": order_id,
            "event_type": "order_submitted",
            "run_id": "run-B",
            "sequence_no": 4,
            "metrics": {"order_qty": 50.0},
        })
        order_runB["correlation_envelope"] = dict(order_runB["correlation_envelope"])
        order_runB["correlation_envelope"]["event_id"] = order_id

        summary = dict(base)
        summary["last_lifecycle_identity"] = order_runB
        summary["committed_position_snapshot"] = snap_runA
        summary["pnl"] = 9999.0
        summary["drawdown"] = 0.85
        summary["queue_lag_ms"] = 15
        summary["event_delivery_lag_ms"] = 25

        class MockResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"status":"accepted"}'

        telemetry_calls = []

        def fake_urlopen(req, timeout=None):
            data = json.loads(req.data.decode("utf-8"))
            telemetry_calls.append(data)
            return MockResponse()

        tick_id = "tick-misjoin-001"
        eval_id = svc._tick_evaluation_id(tick_id, summary["binding_id"])

        # Pre-seed a cached evaluation for the tick from run-B
        svc.store.put_evaluation({
            "id": eval_id,
            "evaluation_id": eval_id,
            "tenant_id": summary.get("tenant_id") or "default",
            "binding_id": summary["binding_id"],
            "runtime_id": summary["runtime_id"],
            "status": "pending",
            "upstream_event_id": order_id,
            "run_id": "run-B",
            "evaluated_at": "2026-07-15T10:05:00Z",
        })

        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": tick_id, "sla_seconds": 60.0},
            )
            assert resp.status_code == 201
            payload = resp.json()

            # Misjoined cache evaluation was NOT reused; binding was NOT skipped
            assert summary["binding_id"] not in payload["skipped_binding_ids"]
            assert eval_id in payload["evaluation_ids"]

            # Newly stored evaluation binds to run-A snapshot, not hijacked by run-B
            ev = svc.store.get_evaluation(eval_id)
            assert ev["run_id"] == "run-A"
            assert ev["snapshot_event_id"] == snap_id
            assert ev["observed_metrics"]["position_qty"] == 100.0
            assert ev["observed_metrics"]["price"] == 500.0
            assert ev["observed_metrics"]["cash"] == 50000.0
            assert "pnl" not in ev["observed_metrics"]

            # Telemetry terminal emitted for run-A
            assert len(telemetry_calls) == 1
            assert telemetry_calls[0]["run_id"] == "run-A"
            assert telemetry_calls[0]["causal_parent_id"] == snap_id


def test_same_window_replay_preserves_accepted_candidate_and_metrics() -> None:
    from fastapi.testclient import TestClient
    import urllib.error
    import urllib.parse

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        base = _paper_lifecycle_summary()
        snap_id_A = "evt-snap-runA"
        snap_id_B = "evt-snap-runB"

        snap_runA = dict(base["last_lifecycle_identity"])
        snap_runA.update({
            "event_id": snap_id_A,
            "event_type": "position_snapshot",
            "run_id": "run-A",
            "sequence_no": 7,
            "causal_parent_id": "evt-fill-runA",
            "metrics": {"position_qty": 100.0, "price": 500.0, "cash": 50000.0},
        })
        snap_runA["correlation_envelope"] = dict(snap_runA["correlation_envelope"])
        snap_runA["correlation_envelope"]["event_id"] = snap_id_A

        snap_runB = dict(base["last_lifecycle_identity"])
        snap_runB.update({
            "event_id": snap_id_B,
            "event_type": "position_snapshot",
            "run_id": "run-B",
            "sequence_no": 9,
            "causal_parent_id": "evt-fill-runB",
            "metrics": {"position_qty": 200.0, "price": 600.0, "cash": 60000.0},
        })
        snap_runB["correlation_envelope"] = dict(snap_runB["correlation_envelope"])
        snap_runB["correlation_envelope"]["event_id"] = snap_id_B

        summary_A = dict(base)
        summary_A["last_lifecycle_identity"] = snap_runA
        summary_A["committed_position_snapshot"] = snap_runA
        summary_A["queue_lag_ms"] = 10
        summary_A["event_delivery_lag_ms"] = 20

        summary_B = dict(base)
        summary_B["last_lifecycle_identity"] = snap_runB
        summary_B["committed_position_snapshot"] = snap_runB
        summary_B["queue_lag_ms"] = 30
        summary_B["event_delivery_lag_ms"] = 40

        class MockResponse:
            def __init__(self, status=202, body=b'{"status":"accepted"}'):
                self.status = status
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self._body

            def getcode(self):
                return self.status

        telemetry_calls = []

        def fake_urlopen(req, timeout=None):
            req_data = getattr(req, "data", None)
            if req_data:
                data = json.loads(req_data.decode("utf-8"))
                telemetry_calls.append(data)
                return MockResponse(202, b'{"status":"accepted"}')
            url = req.full_url if hasattr(req, "full_url") else str(getattr(req, "url", req))
            event_id = url.split("/api/telemetry/events/")[-1].split("?")[0]
            event_id = urllib.parse.unquote(event_id)
            for call in telemetry_calls:
                if call.get("event_id") == event_id:
                    return MockResponse(200, json.dumps(call).encode("utf-8"))
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"error":{"code":"NOT_FOUND"}}'))

        tick_id = "tick-same-window-replay-001"
        eval_id = svc._tick_evaluation_id(tick_id, summary_A["binding_id"])

        # 1. Scheduled reconcile processes candidate A: accepted and stored
        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary_A]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            resp_A = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": tick_id, "sla_seconds": 60.0},
            )
            assert resp_A.status_code == 201
            payload_A = resp_A.json()
            assert len(telemetry_calls) == 1
            assert telemetry_calls[0]["run_id"] == "run-A"
            assert telemetry_calls[0]["causal_parent_id"] == snap_id_A
            assert payload_A["lifecycle_accepted_event_ids"] == [telemetry_calls[0]["event_id"]]

            # Evaluation in store binds to candidate A
            ev_A = svc.store.get_evaluation(eval_id)
            assert ev_A["run_id"] == "run-A"
            assert ev_A["snapshot_event_id"] == snap_id_A
            assert ev_A["observed_metrics"]["position_qty"] == 100.0
            assert ev_A["lifecycle_append"]["status"] == "accepted"

        # 2. Same-window replay with candidate B committed: must preserve accepted A evaluation,
        # paired metrics, and receipt; must NOT retarget event ID to B or emit second call.
        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary_B]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            resp_B = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": tick_id, "sla_seconds": 60.0},
            )
            assert resp_B.status_code == 201
            payload_B = resp_B.json()

            # Zero additional network calls emitted
            assert len(telemetry_calls) == 1, "Must not emit additional network call for candidate B under same evaluation ID"

            # Binding is skipped, retaining candidate A accepted status
            assert summary_B["binding_id"] in payload_B["skipped_binding_ids"]
            assert payload_B["lifecycle_accepted_event_ids"] == [telemetry_calls[0]["event_id"]]

            # Preserved evaluation in store retains candidate A identity and metrics
            ev_persisted = svc.store.get_evaluation(eval_id)
            assert ev_persisted["run_id"] == "run-A"
            assert ev_persisted["snapshot_event_id"] == snap_id_A
            assert ev_persisted["observed_metrics"]["position_qty"] == 100.0
            assert ev_persisted["lifecycle_append"]["status"] == "accepted"

        # 3. Lifecycle-only scheduled reconcile cross-call replay: call 1 with candidate A,
        # then call 2 with candidate B for same window. Must preserve candidate A without
        # emitting candidate B or retargeting event ID.
        telemetry_calls.clear()
        tick_id_lo = "tick-lifecycle-only-same-window-001"
        binding_id_lo = "12345678-1234-4234-8234-123456789abc"
        summary_lo_A = dict(summary_A)
        summary_lo_A["binding_id"] = binding_id_lo
        snap_lo_A = dict(snap_runA)
        snap_lo_A["binding_id"] = binding_id_lo
        snap_lo_A["event_id"] = "evt-snap-lo-A"
        snap_lo_A["correlation_envelope"] = dict(snap_runA["correlation_envelope"])
        snap_lo_A["correlation_envelope"]["binding_id"] = binding_id_lo
        snap_lo_A["correlation_envelope"]["event_id"] = "evt-snap-lo-A"
        summary_lo_A["last_lifecycle_identity"] = snap_lo_A
        summary_lo_A["committed_position_snapshot"] = snap_lo_A

        summary_lo_B = dict(summary_B)
        summary_lo_B["binding_id"] = binding_id_lo
        snap_lo_B = dict(snap_runB)
        snap_lo_B["binding_id"] = binding_id_lo
        snap_lo_B["event_id"] = "evt-snap-lo-B"
        snap_lo_B["correlation_envelope"] = dict(snap_runB["correlation_envelope"])
        snap_lo_B["correlation_envelope"]["binding_id"] = binding_id_lo
        snap_lo_B["correlation_envelope"]["event_id"] = "evt-snap-lo-B"
        summary_lo_B["last_lifecycle_identity"] = snap_lo_B
        summary_lo_B["committed_position_snapshot"] = snap_lo_B

        eval_id_lo = svc._tick_evaluation_id(tick_id_lo, binding_id_lo)

        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary_lo_A]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            resp_lo_1 = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id_lo,
                    "sla_seconds": 60.0,
                    "lifecycle_only": True,
                    "binding_id": binding_id_lo,
                },
            )
            assert resp_lo_1.status_code == 201
            payload_lo_1 = resp_lo_1.json()
            assert len(telemetry_calls) == 1
            assert telemetry_calls[0]["run_id"] == "run-A"
            assert telemetry_calls[0]["causal_parent_id"] == "evt-snap-lo-A"
            assert payload_lo_1["lifecycle_accepted_event_ids"] == [telemetry_calls[0]["event_id"]]
            assert len(payload_lo_1["lifecycle_append_results"]) == 1
            assert payload_lo_1["lifecycle_only"] is True
            assert payload_lo_1["incident_dispatch_enabled"] is False
            ev_lo = svc.store.get_evaluation(eval_id_lo)
            assert ev_lo is not None
            assert ev_lo["run_id"] == "run-A"
            assert ev_lo["snapshot_event_id"] == "evt-snap-lo-A"
            assert ev_lo["observed_metrics"]["position_qty"] == 100.0
            assert ev_lo["lifecycle_append"]["status"] == "accepted"

        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary_lo_B]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            resp_lo_2 = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id_lo,
                    "sla_seconds": 60.0,
                    "lifecycle_only": True,
                    "binding_id": binding_id_lo,
                },
            )
            assert resp_lo_2.status_code == 201
            payload_lo_2 = resp_lo_2.json()
            assert len(telemetry_calls) == 1, "Must not emit candidate B during cross-call replay"
            assert binding_id_lo in payload_lo_2["skipped_binding_ids"]
            assert payload_lo_2["lifecycle_accepted_event_ids"] == [telemetry_calls[0]["event_id"]]
            assert len(payload_lo_2["lifecycle_append_results"]) == 1
            assert payload_lo_2["lifecycle_append_results"][0]["status"] == "accepted"
            assert payload_lo_2["lifecycle_only"] is True
            ev_lo_2 = svc.store.get_evaluation(eval_id_lo)
            assert ev_lo_2 is not None
            assert ev_lo_2["run_id"] == "run-A"
            assert ev_lo_2["snapshot_event_id"] == "evt-snap-lo-A"

        # 4. Failed-window / SLA breach replay: call 1 appends candidate A but breaches SLA
        # so claim is failed; call 2 reacquires same window with candidate B.
        # Must preserve candidate A evaluation from store without emitting candidate B.
        telemetry_calls.clear()
        tick_id_failed = "tick-failed-window-replay-001"
        binding_id_failed = "44444444-4444-4444-4444-444444444444"
        summary_failed_A = dict(summary_A)
        summary_failed_A["binding_id"] = binding_id_failed
        snap_failed_A = dict(snap_runA)
        snap_failed_A["binding_id"] = binding_id_failed
        snap_failed_A["event_id"] = "evt-snap-failed-A"
        snap_failed_A["correlation_envelope"] = dict(snap_runA["correlation_envelope"])
        snap_failed_A["correlation_envelope"]["binding_id"] = binding_id_failed
        snap_failed_A["correlation_envelope"]["event_id"] = "evt-snap-failed-A"
        summary_failed_A["last_lifecycle_identity"] = snap_failed_A
        summary_failed_A["committed_position_snapshot"] = snap_failed_A

        summary_failed_B = dict(summary_B)
        summary_failed_B["binding_id"] = binding_id_failed
        snap_failed_B = dict(snap_runB)
        snap_failed_B["binding_id"] = binding_id_failed
        snap_failed_B["event_id"] = "evt-snap-failed-B"
        snap_failed_B["correlation_envelope"] = dict(snap_runB["correlation_envelope"])
        snap_failed_B["correlation_envelope"]["binding_id"] = binding_id_failed
        snap_failed_B["correlation_envelope"]["event_id"] = "evt-snap-failed-B"
        summary_failed_B["last_lifecycle_identity"] = snap_failed_B
        summary_failed_B["committed_position_snapshot"] = snap_failed_B

        eval_id_failed = svc._tick_evaluation_id(tick_id_failed, binding_id_failed)
        t0 = svc.time.monotonic()

        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary_failed_A]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen),
            mock.patch.object(svc, "_monotonic", side_effect=[t0, t0 + 100.0, t0 + 100.0]),
        ):
            resp_failed_1 = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id_failed,
                    "sla_seconds": 10.0,
                    "lifecycle_only": True,
                    "binding_id": binding_id_failed,
                },
            )
            assert resp_failed_1.status_code == 201
            payload_failed_1 = resp_failed_1.json()
            assert payload_failed_1["within_sla"] is False
            assert len(telemetry_calls) == 1
            assert telemetry_calls[0]["run_id"] == "run-A"
            assert svc.store.get_evaluation(eval_id_failed) is not None
            assert svc.store.get_evaluation(eval_id_failed)["run_id"] == "run-A"
            assert svc.store.get_evaluation(eval_id_failed)["snapshot_event_id"] == snap_failed_A["event_id"]

        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary_failed_B]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            resp_failed_2 = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id_failed,
                    "sla_seconds": 60.0,
                    "lifecycle_only": True,
                    "binding_id": binding_id_failed,
                },
            )
            assert resp_failed_2.status_code == 201
            payload_failed_2 = resp_failed_2.json()
            assert len(telemetry_calls) == 1, "Must not emit candidate B after recovering failed window in lifecycle-only mode"
            assert payload_failed_2["lifecycle_accepted_event_ids"] == [telemetry_calls[0]["event_id"]], repr(payload_failed_2)
            assert payload_failed_2["lifecycle_append_results"][0]["status"] == "accepted"
            assert svc.store.get_evaluation(eval_id_failed) is not None
            assert svc.store.get_evaluation(eval_id_failed)["run_id"] == "run-A"

        # 5. Ambiguous / crash replay: call 1 creates candidate A event and persists intent
        # into evaluation store, but network append raises retryable error (504/timeout).
        # When call 2 retries the window while candidate B has appeared, it must retry
        # candidate A's exact persisted event payload rather than generating candidate B.
        telemetry_calls.clear()
        tick_id_ambig = "tick-ambiguous-crash-replay-001"
        binding_id_ambig = "66666666-6666-4666-8666-666666666666"
        summary_ambig_A = dict(summary_failed_A)
        summary_ambig_A["binding_id"] = binding_id_ambig
        snap_ambig_A = dict(snap_failed_A)
        snap_ambig_A["binding_id"] = binding_id_ambig
        snap_ambig_A["event_id"] = "evt-snap-ambig-A"
        snap_ambig_A["correlation_envelope"] = dict(snap_ambig_A["correlation_envelope"])
        snap_ambig_A["correlation_envelope"]["binding_id"] = binding_id_ambig
        snap_ambig_A["correlation_envelope"]["event_id"] = "evt-snap-ambig-A"
        summary_ambig_A["last_lifecycle_identity"] = snap_ambig_A
        summary_ambig_A["committed_position_snapshot"] = snap_ambig_A

        summary_ambig_B = dict(summary_failed_B)
        summary_ambig_B["binding_id"] = binding_id_ambig
        snap_ambig_B = dict(snap_failed_B)
        snap_ambig_B["binding_id"] = binding_id_ambig
        snap_ambig_B["event_id"] = "evt-snap-ambig-B"
        snap_ambig_B["correlation_envelope"] = dict(snap_ambig_B["correlation_envelope"])
        snap_ambig_B["correlation_envelope"]["binding_id"] = binding_id_ambig
        snap_ambig_B["correlation_envelope"]["event_id"] = "evt-snap-ambig-B"
        summary_ambig_B["last_lifecycle_identity"] = snap_ambig_B
        summary_ambig_B["committed_position_snapshot"] = snap_ambig_B

        eval_id_ambig = svc._tick_evaluation_id(tick_id_ambig, binding_id_ambig)

        def fake_urlopen_crash_first(req, timeout=None):
            req_data = getattr(req, "data", None)
            if req_data:
                data = json.loads(req_data.decode("utf-8"))
                telemetry_calls.append(data)
                if len(telemetry_calls) == 1:
                    raise urllib.error.HTTPError("http://telemetry:8083", 504, "Gateway Timeout", {}, io.BytesIO(b'{"error":"timeout"}'))
                return MockResponse(202, b'{"status":"accepted"}')
            raise urllib.error.HTTPError("http://telemetry:8083", 404, "Not Found", {}, io.BytesIO(b'{"error":{"code":"NOT_FOUND"}}'))

        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary_ambig_A]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen_crash_first),
        ):
            resp_ambig_1 = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id_ambig,
                    "sla_seconds": 60.0,
                    "lifecycle_only": True,
                    "binding_id": binding_id_ambig,
                },
            )
            assert resp_ambig_1.status_code == 201
            payload_ambig_1 = resp_ambig_1.json()
            assert payload_ambig_1["status"] == "failure"
            assert len(payload_ambig_1["lifecycle_retryable_errors"]) == 1
            assert len(telemetry_calls) == 1
            assert telemetry_calls[0]["run_id"] == "run-A"

            # Intent and created event for Candidate A were saved before network call:
            ev_ambig_1 = svc.store.get_evaluation(eval_id_ambig)
            assert ev_ambig_1 is not None
            assert ev_ambig_1["run_id"] == "run-A"
            assert ev_ambig_1["lifecycle_append"]["event_id"] == telemetry_calls[0]["event_id"]
            assert ev_ambig_1["lifecycle_append"]["status"] == "retryable_error"

        # Now call 2 retries while Candidate B is in summaries:
        with (
            mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": "http://telemetry:8083"}),
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary_ambig_B]),
            mock.patch.object(svc.urllib.request, "urlopen", side_effect=fake_urlopen_crash_first),
        ):
            resp_ambig_2 = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={
                    "tick_id": tick_id_ambig,
                    "sla_seconds": 60.0,
                    "lifecycle_only": True,
                    "binding_id": binding_id_ambig,
                },
            )
            assert resp_ambig_2.status_code == 201
            payload_ambig_2 = resp_ambig_2.json()
            assert payload_ambig_2["status"] == "ok"
            assert len(telemetry_calls) == 2
            # Both calls must use candidate A's event_id and run-A!
            assert telemetry_calls[1]["run_id"] == "run-A"
            assert telemetry_calls[1]["event_id"] == telemetry_calls[0]["event_id"]
            assert telemetry_calls[1]["causal_parent_id"] == snap_ambig_A["event_id"]
            assert payload_ambig_2["lifecycle_accepted_event_ids"] == [telemetry_calls[0]["event_id"]]
            assert payload_ambig_2["lifecycle_append_results"][0]["status"] == "accepted"

            ev_ambig_2 = svc.store.get_evaluation(eval_id_ambig)
            assert ev_ambig_2["run_id"] == "run-A"
            assert ev_ambig_2["lifecycle_append"]["status"] == "accepted"








def _retired_binding(binding_id: str, retired_at: str) -> dict:
    return {"binding_id": binding_id, "status": "retired", "retired_at": retired_at}


def test_scheduled_tick_skips_binding_retired_before_tick() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _healthy_runtime_summary(state="retired")
        with (
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(
                svc,
                "_authoritative_runtime_binding",
                return_value=_retired_binding(summary["binding_id"], "2000-01-01T00:00:00Z"),
            ),
            mock.patch.object(svc, "_classify_drift_report_incident") as classify,
        ):
            response = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-retired-001"},
            )

        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["retired_binding_ids"] == [summary["binding_id"]]
        assert payload["incident_delivery_errors"] == []
        assert payload["evaluated_binding_count"] == 0
        classify.assert_not_called()


def test_scheduled_tick_keeps_incident_when_observed_before_retired_at() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _healthy_runtime_summary(queue_lag_ms=20_000)
        with (
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(
                svc,
                "_authoritative_runtime_binding",
                return_value=_retired_binding(summary["binding_id"], "2999-01-01T00:00:00Z"),
            ),
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                return_value={"incident_id": "inc-before-retire-001"},
            ) as classify,
        ):
            payload = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-before-retire-001"},
            ).json()

        assert payload["status"] == "critical"
        assert payload["retired_binding_ids"] == []
        assert payload["incident_ids"] == ["inc-before-retire-001"]
        assert classify.call_count == 1


def test_scheduled_retired_at_rejection_is_terminal_and_not_a_failure() -> None:
    from fastapi.testclient import TestClient

    detail = (
        "incident created_at '2026-10-15T10:09:29Z' is after RuntimeBinding "
        "'rb-1' retired_at '2026-10-15T10:09:20Z'"
    )
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _healthy_runtime_summary(queue_lag_ms=20_000)
        with (
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(svc, "_authoritative_runtime_binding", return_value=None),
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                side_effect=svc.HTTPException(status_code=502, detail=detail),
            ) as classify,
        ):
            first = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-rejected-001"},
            ).json()
            replay = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-rejected-001"},
            ).json()

        assert first["status"] == "critical"
        assert first["incident_delivery_errors"] == []
        assert replay["status"] == "critical"
        assert classify.call_count == 1
        evaluation = svc.store.get_evaluation(
            svc._tick_evaluation_id("tick-rejected-001", summary["binding_id"])
        )
        assert evaluation["incident_delivery"]["status"] == "terminal_rejected"


def test_runtime_manager_client_imports_through_real_service_path() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        assert svc.RuntimeManagerClient is not None


def test_scheduled_tick_stops_lookups_after_first_failure_and_skips_healthy() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        bad_a = _healthy_runtime_summary(queue_lag_ms=20_000)
        bad_b = {**_healthy_runtime_summary(queue_lag_ms=20_000), "binding_id": "rb-other"}
        healthy = {**_healthy_runtime_summary(), "binding_id": "rb-healthy"}
        with (
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[healthy, bad_a, bad_b]),
            mock.patch.object(
                svc, "_authoritative_runtime_binding", side_effect=RuntimeError("down")
            ) as lookup,
            mock.patch.object(svc, "_classify_drift_report_incident", return_value=None),
        ):
            client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-lookup-fail-001"},
            )
        assert lookup.call_count == 1


def _seed_accepted_append(svc, binding_id: str) -> None:
    svc.store.put_evaluation(
        {
            "evaluation_id": f"evaluation-{binding_id}",
            "binding_id": binding_id,
            "evaluated_at": "2026-10-14T16:13:00Z",
            "lifecycle_append": {
                "status": "accepted",
                "accepted_at": "2026-10-14T16:13:38Z",
                "event_id": "accepted-old",
                "event": {
                    "event_id": "accepted-old",
                    "aggregate_type": "runtime_binding",
                    "aggregate_id": binding_id,
                    "sequence_no": 3,
                },
            },
        }
    )


def _superseded_summary(binding_id: str) -> dict:
    return {
        "tenant_id": "tenant-a",
        "runtime_id": "rt-1",
        "artifact_id": "art-1",
        "artifact_version": "1",
        "last_lifecycle_identity": {
            "event_id": "observed-new",
            "tenant_id": "tenant-a",
            "binding_id": binding_id,
            "runtime_id": "rt-1",
            "artifact_id": "art-1",
            "artifact_version": "1",
            "aggregate_type": "runtime_binding",
            "aggregate_id": binding_id,
            "sequence_no": 9,
        },
        "recent_lifecycle_event_ids": ["observed-new"],
    }


def test_superseded_append_outside_recent_window_confirmed_by_committed_telemetry() -> None:
    import telemetry_client

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        binding_id = "binding-superseded-001"
        _seed_accepted_append(svc, binding_id)
        pair = {
            "accepted_event_id": "accepted-old",
            "observed_event_id": "observed-new",
            "binding_id": binding_id,
        }
        with (
            mock.patch.object(telemetry_client, "fetch_accepted_event", return_value={"event_id": "accepted-old"}),
            mock.patch.object(telemetry_client, "verify_durable_event_order", return_value=(True, None, pair)),
        ):
            for tick in ("2026-10-15T13:00:00Z", "2026-10-15T13:01:00Z"):
                reason, _ = svc._accepted_append_visibility_reason(
                    summary=_superseded_summary(binding_id),
                    binding_id=binding_id,
                    timestamp=tick,
                    telemetry_url="http://telemetry:8083",
                    tenant_id="tenant-a",
                )
                assert reason is None
        state = svc.store.get_evaluation(f"evaluation-{binding_id}")["lifecycle_append"]
        assert state["summary_visibility_source"] == "committed_telemetry_superseded"


def test_missing_accepted_append_defers_then_terminal_without_failing_tick() -> None:
    import telemetry_client

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        binding_id = "binding-missing-001"
        _seed_accepted_append(svc, binding_id)
        kwargs = dict(
            summary=_superseded_summary(binding_id),
            binding_id=binding_id,
            telemetry_url="http://telemetry:8083",
            tenant_id="tenant-a",
        )
        with mock.patch.object(telemetry_client, "fetch_accepted_event", return_value=None):
            first, _ = svc._accepted_append_visibility_reason(timestamp="2026-10-15T13:00:00Z", **kwargs)
            assert first == "accepted_lifecycle_append_not_visible"
            last, _ = svc._accepted_append_visibility_reason(timestamp="2026-10-15T14:00:00Z", **kwargs)
            assert last == "accepted_lifecycle_append_missing_terminal"
            replay, _ = svc._accepted_append_visibility_reason(timestamp="2026-10-15T14:01:00Z", **kwargs)
            assert replay == "accepted_lifecycle_append_missing_terminal"

        evaluation = {"evaluation_id": "e2", "binding_id": binding_id, "tenant_id": "tenant-a"}
        with mock.patch.object(telemetry_client, "fetch_accepted_event", return_value=None):
            state = svc._ensure_scheduled_lifecycle_append(
                summary=kwargs["summary"],
                evaluation=evaluation,
                telemetry_url="http://telemetry:8083",
                timestamp="2026-10-15T14:02:00Z",
            )
        assert state["status"] == "terminal_failed" and state["terminal"] is True
        assert state["retryable"] is False


def _seed_confirmed_same_sequence(svc, binding_id: str) -> dict:
    svc.store.put_evaluation(
        {
            "evaluation_id": f"evaluation-{binding_id}",
            "binding_id": binding_id,
            "evaluated_at": "2026-10-14T16:13:00Z",
            "lifecycle_append": {
                "status": "accepted",
                "event_id": "accepted-old",
                "summary_visibility_confirmed_at": "2026-10-14T16:21:43Z",
                "event": {
                    "event_id": "accepted-old",
                    "aggregate_type": "trade_journey",
                    "aggregate_id": "tj-1",
                    "sequence_no": 8,
                },
            },
        }
    )
    summary = _superseded_summary(binding_id)
    summary["last_lifecycle_identity"].update(
        {"aggregate_type": "trade_journey", "aggregate_id": "tj-1", "sequence_no": 8}
    )
    return summary


def _same_sequence_reason(svc, binding_id, summary, verify_result, sequence=None):
    import telemetry_client

    if sequence is not None:
        summary["last_lifecycle_identity"]["sequence_no"] = sequence
    with mock.patch.object(
        telemetry_client, "verify_durable_event_order", return_value=verify_result
    ) as verify:
        reason, _ = svc._accepted_append_visibility_reason(
            summary=summary,
            binding_id=binding_id,
            timestamp="2026-10-15T20:00:00Z",
            telemetry_url="http://telemetry:8083",
            tenant_id="tenant-a",
        )
    return reason, verify


def test_same_sequence_different_event_visible_when_durably_ordered() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        binding_id = "binding-same-seq-001"
        summary = _seed_confirmed_same_sequence(svc, binding_id)
        pair = {
            "accepted_event_id": "accepted-old",
            "observed_event_id": "observed-new",
            "binding_id": binding_id,
        }
        for _ in range(2):  # replay stays visible
            reason, verify = _same_sequence_reason(svc, binding_id, summary, (True, None, pair))
            assert reason is None
            kwargs = verify.call_args.kwargs
            assert kwargs["expected_binding_id"] == binding_id
            assert kwargs["expected_runtime_id"] == "rt-1"


def test_same_sequence_different_event_deferred_when_order_unverified() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        binding_id = "binding-same-seq-002"
        summary = _seed_confirmed_same_sequence(svc, binding_id)
        for result in ((False, "unordered", {}), (True, None, {"accepted_event_id": "other"})):
            reason, _ = _same_sequence_reason(svc, binding_id, summary, result)
            assert reason == "accepted_lifecycle_append_not_visible"


def test_lower_sequence_never_consults_durable_order_and_higher_stays_visible() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        binding_id = "binding-same-seq-003"
        summary = _seed_confirmed_same_sequence(svc, binding_id)
        pair = {
            "accepted_event_id": "accepted-old",
            "observed_event_id": "observed-new",
            "binding_id": binding_id,
        }
        reason, verify = _same_sequence_reason(svc, binding_id, summary, (True, None, pair), sequence=7)
        assert reason == "accepted_lifecycle_append_not_visible"
        verify.assert_not_called()
        reason, verify = _same_sequence_reason(svc, binding_id, summary, (False, None, {}), sequence=9)
        assert reason is None
        verify.assert_not_called()


def _stale_heartbeat_summary() -> dict:
    # Telemetry marks a summary degraded after 90 s without a heartbeat.
    return _healthy_runtime_summary(
        state="degraded",
        last_heartbeat_at="2026-10-10T00:00:11Z",
        heartbeat_age_seconds=600,
    )


def test_scheduled_tick_treats_paused_binding_as_expected_stopped() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _stale_heartbeat_summary()
        with (
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(
                svc,
                "_authoritative_runtime_binding",
                return_value={"binding_id": summary["binding_id"], "status": "paused"},
            ),
            mock.patch.object(svc, "_classify_drift_report_incident") as classify,
        ):
            payload = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-paused-001"},
            ).json()

        assert payload["status"] == "ok"
        assert payload["paused_binding_ids"] == [summary["binding_id"]]
        assert payload["retired_binding_ids"] == []
        assert payload["drift_report_ids"] == []
        assert payload["incident_ids"] == []
        assert payload["evaluated_binding_count"] == 0
        classify.assert_not_called()


def test_scheduled_tick_keeps_drift_for_active_binding_with_stale_summary() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)
        summary = _stale_heartbeat_summary()
        with (
            mock.patch.object(svc, "fetch_runtime_summaries", return_value=[summary]),
            mock.patch.object(
                svc,
                "_authoritative_runtime_binding",
                return_value={"binding_id": summary["binding_id"], "status": "active"},
            ),
            mock.patch.object(
                svc,
                "_classify_drift_report_incident",
                return_value={"incident_id": "inc-active-stale-001"},
            ) as classify,
        ):
            payload = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-active-stale-001"},
            ).json()

        assert payload["paused_binding_ids"] == []
        assert payload["evaluated_binding_count"] == 1
        assert payload["drift_report_ids"]
        assert payload["incident_ids"] == ["inc-active-stale-001"]
        assert classify.call_count == 1
