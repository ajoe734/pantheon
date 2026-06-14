from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]
FIXTURE_PATH = SERVICE_DIR / "fixtures" / "devloop-drift-telemetry-event.json"


def _load_consumer_module():
    sys.modules.pop("reconciliation_drift_consumer_test", None)
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_consumer_test",
        SERVICE_DIR / "consumer.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconciliation_drift_consumer_test"] = module
    spec.loader.exec_module(module)
    return module


def _load_service_module(data_dir: str):
    sys.modules.pop("consumer", None)
    sys.modules.pop("store", None)
    sys.modules.pop("reconciliation_drift_consume_test_main", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "reconciliation_drift_consume_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["reconciliation_drift_consume_test_main"] = module
        with mock.patch.dict(
            "os.environ",
            {
                "RECONCILIATION_DRIFT_DATA_DIR": data_dir,
                "RECONCILIATION_DRIFT_STORE_BACKEND": "json",
                "PERSISTENCE_POSTURE": "lenient",
            },
        ):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("consumer", None)
        sys.modules.pop("store", None)


def test_telemetry_fixture_builds_canonical_drift_report() -> None:
    consumer = _load_consumer_module()

    events = consumer.load_telemetry_events_from_path(FIXTURE_PATH)
    assert len(events) == 1

    report = consumer.build_drift_report_from_event(events[0], existing_report_ids=set())

    assert report is not None
    assert report["drift_report_id"] == "drift-evt-devloop-reconcile-live-001"
    assert report["recon_run_id"] == "recon-evt-devloop-reconcile-live-001"
    assert report["drift_type"] == "slippage"
    assert report["scope_ref"] == "rtb-devloop-reconcile-001"
    assert report["baseline_ref"] == "paper-baseline-devloop-reconcile-001"
    assert report["current_ref"] == "live-window-devloop-reconcile-001"
    assert report["severity"] == "critical"
    assert report["status"] == "open"
    assert report["recommended_action"] == "open_incident"
    assert report["source_contract"]["telemetry_truth_owner"] == "telemetry-ingest"
    assert report["source_contract"]["emergency_control_chain_affected"] is False
    assert "telemetry_event:evt-devloop-reconcile-live-001" in report["evidence_refs"]
    assert set(report["metrics"]["breached_metric_ids"]) == {"avg_slippage_bps", "drawdown", "pnl"}


def test_consume_endpoint_persists_drift_report_from_telemetry_fixture() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        module = _load_service_module(data_dir)
        client = TestClient(module.app)
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

        consumed = client.post(
            "/api/reconciliation-drift/telemetry-events/consume",
            json={"events": [fixture]},
        )
        assert consumed.status_code == 201, consumed.text
        payload = consumed.json()
        assert payload["consumed_event_count"] == 1
        assert payload["drift_report_count"] == 1
        assert payload["ignored_event_ids"] == []

        report = payload["drift_reports"][0]
        assert report["drift_report_id"] == "drift-evt-devloop-reconcile-live-001"
        assert report["deployment_stage"] == "live"
        assert report["binding_id"] == "rtb-devloop-reconcile-001"
        assert report["runtime_id"] == "runtime-devloop-reconcile-001"
        assert report["metrics"]["current_metrics"]["avg_slippage_bps"] == 4.8
        assert report["metrics"]["baseline_metrics"]["avg_slippage_bps"] == 2.0

        listed = client.get(
            "/api/reconciliation-drift/drift-reports",
            params={"scope_ref": "rtb-devloop-reconcile-001"},
        )
        assert listed.status_code == 200
        assert [item["drift_report_id"] for item in listed.json()] == [
            "drift-evt-devloop-reconcile-live-001"
        ]

        fetched = client.get("/api/reconciliation-drift/drift-reports/drift-evt-devloop-reconcile-live-001")
        assert fetched.status_code == 200
        assert fetched.json()["recommended_action"] == "open_incident"

        summary = client.get("/api/reconciliation-drift/summary", params={"binding_id": "rtb-devloop-reconcile-001"})
        assert summary.status_code == 200
        assert summary.json()["drift_report_count"] == 1

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["drift_report_count"] == 1
