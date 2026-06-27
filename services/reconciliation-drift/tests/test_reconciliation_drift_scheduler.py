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

        with mock.patch.dict("os.environ", {"PANTHEON_TELEMETRY_API_URL": ""}):
            with mock.patch.object(svc, "PANTHEON_TELEMETRY_API_URL", "", create=True):
                resp = client.post(
                    "/api/reconciliation-drift/scheduled-reconcile",
                    json={"tick_id": "tick-test-001"},
                )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["tick_id"] == "tick-test-001"
        assert payload["evaluated_binding_count"] == 0
        assert payload["skipped_binding_count"] == 0
        assert payload["evaluation_ids"] == []


def test_scheduled_reconcile_with_telemetry_summaries() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        fake_summaries = [
            {
                "binding_id": "rtb-sched-test-001",
                "runtime_id": "runtime-sched-001",
                "telemetry_event_ids": ["evt-001", "evt-002"],
                "observed_metrics": {"avg_slippage_bps": 1.5},
                "baseline_metrics": {"avg_slippage_bps": 2.0},
            },
            {
                "binding_id": "rtb-sched-test-002",
                "runtime_id": "runtime-sched-002",
                "telemetry_event_ids": [],
                "observed_metrics": {},
                "baseline_metrics": {},
            },
        ]

        with mock.patch.object(svc, "_fetch_telemetry_runtime_summaries", return_value=fake_summaries):
            resp = client.post(
                "/api/reconciliation-drift/scheduled-reconcile",
                json={"tick_id": "tick-test-002"},
            )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["tick_id"] == "tick-test-002"
        assert payload["evaluated_binding_count"] == 2
        assert payload["skipped_binding_count"] == 0
        assert len(payload["evaluation_ids"]) == 2

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
        recon_checks = ev["reconciliation_checks"]
        assert len(recon_checks) == 1
        check = recon_checks[0]
        assert check["check"] == "telemetry_runtime_alignment"
        assert check["binding_id"] == "rtb-sched-test-001"
        assert check["runtime_id"] == "runtime-sched-001"
        assert check["telemetry_event_ids"] == ["evt-001", "evt-002"]


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
