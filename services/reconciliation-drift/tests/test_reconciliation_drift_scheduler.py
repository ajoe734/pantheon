from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock


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
