from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_service_module():
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
            spec = importlib.util.spec_from_file_location("reconciliation_drift_test_main", SERVICE_DIR / "main.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["reconciliation_drift_test_main"] = module
            spec.loader.exec_module(module)
            return module
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass


def test_incident_post_honors_configured_timeout() -> None:
    module = _load_service_module()

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"incident_id":"inc-timeout-test"}'

    with mock.patch.dict(
        "os.environ",
        {"PANTHEON_INCIDENTS_API_TIMEOUT_SECONDS": "71"},
    ), mock.patch.object(
        module.urllib.request,
        "urlopen",
        return_value=_Response(),
    ) as urlopen:
        result = module._post_json(
            "http://incidents:8090/api/incidents/consume-drift-report",
            {"drift_report": {"drift_report_id": "drift-timeout-test"}},
        )

    assert result == {"incident_id": "inc-timeout-test"}
    assert urlopen.call_args.kwargs["timeout"] == 71.0


def test_reconciliation_drift_generates_summary_status_and_alert_handoff() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "reconciliation-drift"
    assert health.json()["emergency_control_chain_member"] is False

    created = client.post(
        "/api/reconciliation-drift/evaluations",
        json={
            "evaluation_id": "rdeval-test-001",
            "binding_id": "binding-1",
            "runtime_id": "runtime-1",
            "baseline_metrics": {"slippage_bps": 10.0, "fill_ratio": 0.95},
            "telemetry_events": [
                {
                    "event_id": "evt-1",
                    "binding_id": "binding-1",
                    "runtime_id": "runtime-1",
                    "metrics": {"slippage_bps": 18.0, "fill_ratio": 0.90},
                },
                {
                    "event_id": "evt-2",
                    "binding_id": "binding-1",
                    "runtime_id": "runtime-1",
                    "metrics": {"slippage_bps": 20.0, "fill_ratio": 0.88},
                },
            ],
            "lineage_projection": {"target_id": "binding-1", "derived_only": True},
            "runtime_evidence": {"binding_id": "binding-1", "runtime_id": "runtime-1", "status": "active"},
            "thresholds": {
                "slippage_bps": {
                    "warning_relative_delta": 0.20,
                    "critical_relative_delta": 0.50,
                }
            },
            "evaluated_at": "2026-04-28T22:00:00Z",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["status"] == "critical"
    assert payload["observed_metrics"]["slippage_bps"] == 19.0
    assert payload["source_contract"]["derived_only"] is True
    assert payload["source_contract"]["emergency_control_chain_affected"] is False

    summary = client.get("/api/reconciliation-drift/summary", params={"binding_id": "binding-1"})
    assert summary.status_code == 200
    assert summary.json()["latest_evaluation_id"] == "rdeval-test-001"
    assert summary.json()["alert_handoff_count"] == 1

    status = client.get("/api/reconciliation-drift/reconciliation-status/binding-1")
    assert status.status_code == 200
    assert [check["status"] for check in status.json()["checks"]] == ["ok", "ok", "ok", "ok", "ok"]

    alerts = client.get("/api/reconciliation-drift/alerts", params={"binding_id": "binding-1"})
    assert alerts.status_code == 200
    alert = alerts.json()[0]
    assert alert["alert_type"] == "metric_drift"
    assert alert["target_service"] == "evolution"
    assert alert["emergency_control_chain_affected"] is False

    handoff = client.post(
        f"/api/reconciliation-drift/alerts/{alert['alert_id']}/handoff",
        json={"actor_id": "operator", "handoff_state": "sent", "note": "queued for threshold evaluation"},
    )
    assert handoff.status_code == 200
    assert handoff.json()["handoff_state"] == "sent"
    assert handoff.json()["handoff_actor"] == "operator"


def test_reconciliation_drift_marks_degraded_inputs_and_mismatch_alerts() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    created = client.post(
        "/api/reconciliation-drift/evaluations",
        json={
            "evaluation_id": "rdeval-test-002",
            "binding_id": "binding-expected",
            "baseline_metrics": {"pnl": 10.0},
            "telemetry_events": [
                {
                    "event_id": "evt-mismatch",
                    "binding_id": "binding-other",
                    "metrics": {"pnl": 10.5},
                }
            ],
            "lineage_projection": None,
            "runtime_evidence": None,
            "evaluated_at": "2026-04-28T22:10:00Z",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["status"] == "critical"
    assert any(check["status"] == "degraded" for check in payload["reconciliation_checks"])
    assert any(check["check"] == "telemetry_binding_alignment" and check["status"] == "critical" for check in payload["reconciliation_checks"])

    alerts = client.get("/api/reconciliation-drift/alerts", params={"binding_id": "binding-expected"})
    assert alerts.status_code == 200
    assert alerts.json()[0]["alert_type"] == "reconciliation_mismatch"
    assert alerts.json()[0]["target_service"] == "incidents"


def test_telemetry_consume_classifies_drift_report_into_incident_service() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"incident_id":"inc-drift-http-001","status":"open"}'

    with mock.patch.dict("os.environ", {"PANTHEON_INCIDENTS_API_URL": "http://incidents:8090"}), mock.patch.object(
        module.urllib.request,
        "urlopen",
        return_value=_Response(),
    ) as urlopen:
        created = client.post(
            "/api/reconciliation-drift/telemetry-events/consume",
            json={
                "event": {
                    "event_id": "evt-drift-http-001",
                    "binding_id": "binding-drift-http-001",
                    "runtime_id": "runtime-drift-http-001",
                    "deployment_stage": "paper",
                    "deployment_plan_id": "plan-drift-http-001",
                    "capital_pool_id": "pool-drift-http-001",
                    "persona_capital_binding_id": "pcb-drift-http-001",
                    "artifact_id": "artifact-drift-http-001",
                    "artifact_version": "1.0.0",
                    "trace_id": "trace-drift-http-001",
                    "metrics": {"rolling_drawdown_multiple": 1.6},
                    "baseline_metrics": {"rolling_drawdown_multiple": 1.0},
                    "thresholds": {
                        "rolling_drawdown_multiple": {
                            "warning_relative_delta": 0.20,
                            "critical_relative_delta": 0.50,
                        }
                    },
                },
                "generated_at": "2026-06-27T15:10:00Z",
            },
        )

    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["drift_report_count"] == 1
    assert payload["incident_case_count"] == 1
    assert payload["incident_cases"][0]["incident_id"] == "inc-drift-http-001"
    request = urlopen.call_args.args[0]
    assert request.full_url == "http://incidents:8090/api/incidents/consume-drift-report"
    posted = json.loads(request.data.decode("utf-8"))
    report = posted["drift_report"]
    assert report["drift_report_id"] == "drift-evt-drift-http-001"
    assert report["binding_id"] == "binding-drift-http-001"
    assert report["runtime_id"] == "runtime-drift-http-001"
    assert report["deployment_plan_id"] == "plan-drift-http-001"
    assert report["telemetry_event_ids"] == ["evt-drift-http-001"]
    assert report["incident_cluster_id"] == "drift:rolling_drawdown_multiple"


def test_paper_run_creates_reconciliation_record_incident_request_and_proposed_evolution() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    created = client.post(
        "/api/reconciliation-drift/paper-runs/reconcile",
        json={
            "record_id": "recon-paper-001",
            "binding_id": "binding-paper-1",
            "runtime_id": "runtime-paper-1",
            "deployment_plan_id": "plan-paper-1",
            "artifact_id": "artifact-paper-1",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-paper-1",
            "persona_capital_binding_id": "pcb-paper-1",
            "trace_id": "trace-paper-1",
            "paper_run_id": "paper-run-001",
            "baseline_ref": "backtest-baseline-001",
            "actual_ref": "paper-runtime-001",
            "baseline_metrics": {"pnl": 100.0},
            "telemetry_events": [
                {
                    "event_id": "evt-paper-1",
                    "binding_id": "binding-paper-1",
                    "runtime_id": "runtime-paper-1",
                    "deployment_plan_id": "plan-paper-1",
                    "artifact_id": "artifact-paper-1",
                    "capital_pool_id": "pool-paper-1",
                    "metrics": {"pnl": 20.0},
                }
            ],
            "thresholds": {"pnl": {"critical_relative_delta": 0.50}},
            "generated_at": "2026-05-01T09:00:00Z",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()

    record = payload["record"]
    assert record["record_id"] == "recon-paper-001"
    assert record["runtime_binding_id"] == "binding-paper-1"
    assert record["artifact_id"] == "artifact-paper-1"
    assert record["capital_pool_id"] == "pool-paper-1"
    assert record["deployment_stage"] == "paper"
    assert record["severity"] == "critical"
    assert record["status"] == "open"

    incident_request = payload["incident_request"]
    assert incident_request["incident_id"] == "recon-paper-001-incident-001"
    assert incident_request["binding_id"] == "binding-paper-1"
    assert incident_request["artifact_id"] == "artifact-paper-1"
    assert incident_request["capital_pool_id"] == "pool-paper-1"
    assert incident_request["telemetry_event_ids"] == ["evt-paper-1"]

    proposal = payload["evolution_proposal"]
    assert proposal["decision_state"] == "proposed"
    assert proposal["metadata"]["proposed_only"] is True
    assert proposal["metadata"]["automatic_execution_allowed"] is False

    records = client.get("/api/reconciliation-drift/reconciliation-records", params={"binding_id": "binding-paper-1"})
    assert records.status_code == 200
    assert records.json()[0]["record_id"] == "recon-paper-001"


def test_paper_run_normalizes_warning_severity_to_record_contract() -> None:
    module = _load_service_module()
    client = TestClient(module.app)

    created = client.post(
        "/api/reconciliation-drift/paper-runs/reconcile",
        json={
            "record_id": "recon-paper-warning",
            "binding_id": "binding-paper-warning",
            "runtime_id": "runtime-paper-warning",
            "deployment_plan_id": "plan-paper-warning",
            "artifact_id": "artifact-paper-warning",
            "artifact_version": "1.0.0",
            "capital_pool_id": "pool-paper-warning",
            "persona_capital_binding_id": "pcb-paper-warning",
            "trace_id": "trace-paper-warning",
            "baseline_metrics": {"pnl": 100.0},
            "telemetry_events": [
                {
                    "event_id": "evt-paper-warning",
                    "binding_id": "binding-paper-warning",
                    "runtime_id": "runtime-paper-warning",
                    "deployment_plan_id": "plan-paper-warning",
                    "artifact_id": "artifact-paper-warning",
                    "capital_pool_id": "pool-paper-warning",
                    "metrics": {"pnl": 75.0},
                }
            ],
            "thresholds": {"pnl": {"warning_relative_delta": 0.20, "critical_relative_delta": 0.50}},
        },
    )

    assert created.status_code == 201, created.text
    record = created.json()["record"]
    assert record["status"] == "open"
    assert record["severity"] == "medium"
    assert record["delta_summary"]["drift_checks"][0]["status"] == "warning"


def test_paper_run_can_open_incident_case_via_incident_service() -> None:
    with mock.patch.dict(
        "os.environ",
        {
            "RECONCILIATION_DRIFT_DATA_DIR": tempfile.mkdtemp(),
            "PANTHEON_INCIDENTS_API_URL": "http://incidents:8090",
        },
    ):
        sys.modules.pop("store", None)
        sys.path.insert(0, str(SERVICE_DIR))
        try:
            spec = importlib.util.spec_from_file_location("reconciliation_drift_incident_test_main", SERVICE_DIR / "main.py")
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["reconciliation_drift_incident_test_main"] = module
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("store", None)
            try:
                sys.path.remove(str(SERVICE_DIR))
            except ValueError:
                pass

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"incident_id":"recon-paper-http-incident-001","status":"open"}'

    with mock.patch.dict("os.environ", {"PANTHEON_INCIDENTS_API_URL": "http://incidents:8090"}), mock.patch.object(
        module.urllib.request,
        "urlopen",
        return_value=_Response(),
    ) as urlopen:
        created = TestClient(module.app).post(
            "/api/reconciliation-drift/paper-runs/reconcile",
            json={
                "record_id": "recon-paper-http",
                "binding_id": "binding-paper-http",
                "runtime_id": "runtime-paper-http",
                "deployment_plan_id": "plan-paper-http",
                "artifact_id": "artifact-paper-http",
                "artifact_version": "1.0.0",
                "capital_pool_id": "pool-paper-http",
                "persona_capital_binding_id": "pcb-paper-http",
                "trace_id": "trace-paper-http",
                "baseline_metrics": {"pnl": 100.0},
                "telemetry_events": [
                    {
                        "event_id": "evt-paper-http",
                        "binding_id": "binding-paper-http",
                        "runtime_id": "runtime-paper-http",
                        "metrics": {"pnl": 20.0},
                    }
                ],
                "thresholds": {"pnl": {"critical_relative_delta": 0.50}},
            },
        )

    assert created.status_code == 201, created.text
    assert created.json()["incident_case"]["incident_id"] == "recon-paper-http-incident-001"
    assert urlopen.call_count == 1
