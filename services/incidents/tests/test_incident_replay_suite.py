"""
Replay suite for LOOP-AUTO-TEL-005.

Proves that:
- Order rejection spike threshold breach opens an IncidentCase.
- Heartbeat loss threshold breach opens an IncidentCase.
- Both replays are idempotent (duplicate replay returns existing incident, count stays 1).
- BFF operator-payload projection agrees with the underlying incident record for
  both scenarios (status, is_open, evidence fields, telemetry_event_ids).
- PnL drift produces a DriftReport when fed through the reconciliation-drift
  consumer, which in turn opens an IncidentCase via consume-drift-report.
- Recovery event (all metrics within threshold) produces no DriftReport and
  therefore no new incident.

Run:
    python3 -m pytest services/incidents/tests/test_incident_replay_suite.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SERVICE_DIR = Path(__file__).resolve().parents[1]
_DRIFT_SERVICE_DIR = _REPO_ROOT / "services" / "reconciliation-drift"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient
from services.incidents.main import app, store

_FIXTURE_DIR = _SERVICE_DIR / "fixtures"
_DRIFT_FIXTURE_DIR = _DRIFT_SERVICE_DIR / "fixtures"

ORDER_REJECTION_FIXTURE = _FIXTURE_DIR / "order_rejection_spike_telemetry.json"
HEARTBEAT_LOSS_FIXTURE = _FIXTURE_DIR / "heartbeat_loss_telemetry.json"
PNL_DRIFT_FIXTURE = _DRIFT_FIXTURE_DIR / "pnl_drift_telemetry_event.json"
RECOVERY_FIXTURE = _DRIFT_FIXTURE_DIR / "recovery_telemetry_event.json"


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    class _AcceptAll:
        def validate_incident(self, incident):
            return None

    monkeypatch.setattr("services.incidents.main.reference_validator", _AcceptAll())
    _reset()
    yield
    _reset()


def _reset():
    store._incidents.clear()
    store._postmortems.clear()
    path = getattr(store, "_path", None)
    if path is not None and path.exists():
        path.unlink()
    if hasattr(store, "_loaded_mtime_ns"):
        store._loaded_mtime_ns = None


client = TestClient(app)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Scenario 1: Order rejection spike
# ---------------------------------------------------------------------------

class TestOrderRejectionSpikeReplay:
    def test_fixture_loads_correctly(self):
        payload = _load(ORDER_REJECTION_FIXTURE)
        assert payload["incident_id"] == "inc-tel005-order-rejection-spike-001"
        assert payload["threshold_snapshot"]["metric_name"] == "rejection_rate_pct"
        assert payload["threshold_snapshot"]["breached"] is True
        assert payload["telemetry_event"]["event_type"] == "order.rejection_spike"

    def test_replay_opens_incident(self):
        payload = _load(ORDER_REJECTION_FIXTURE)
        r = client.post("/api/incidents/consume-threshold", json=payload)
        assert r.status_code == 201, r.text

        body = r.json()
        assert body["incident_id"] == "inc-tel005-order-rejection-spike-001"
        assert body["status"] == "open"
        assert body["severity"] == "critical"
        assert body["binding_id"] == "rtb-tel005-paper-001"
        assert body["deployment_stage"] == "paper"
        assert "tel-tel005-order-reject-spike-001" in body["telemetry_event_ids"]
        assert len(store.list_incidents()) == 1

    def test_replay_is_idempotent(self):
        payload = _load(ORDER_REJECTION_FIXTURE)
        r1 = client.post("/api/incidents/consume-threshold", json=payload)
        r2 = client.post("/api/incidents/consume-threshold", json=payload)

        assert r1.status_code == 201
        assert r2.status_code == 200
        assert r2.json()["incident_id"] == r1.json()["incident_id"]
        assert len(store.list_incidents()) == 1

    def test_operator_payload_agrees_with_incident(self):
        payload = _load(ORDER_REJECTION_FIXTURE)
        client.post("/api/incidents/consume-threshold", json=payload)
        incident_id = "inc-tel005-order-rejection-spike-001"

        inc_r = client.get(f"/api/incidents/{incident_id}")
        op_r = client.get(f"/api/incidents/{incident_id}/operator-payload")

        assert inc_r.status_code == 200
        assert op_r.status_code == 200

        inc = inc_r.json()
        op = op_r.json()

        assert op["incident_id"] == inc["incident_id"]
        assert op["status"] == inc["status"]
        assert op["severity"] == inc["severity"]
        assert op["binding_id"] == inc["binding_id"]
        assert op["deployment_stage"] == inc["deployment_stage"]
        assert op["runtime_id"] == inc["runtime_id"]
        assert op["telemetry_event_ids"] == inc["telemetry_event_ids"]
        assert op["is_open"] is True
        assert op["postmortem_id"] is None


# ---------------------------------------------------------------------------
# Scenario 2: Heartbeat loss
# ---------------------------------------------------------------------------

class TestHeartbeatLossReplay:
    def test_fixture_loads_correctly(self):
        payload = _load(HEARTBEAT_LOSS_FIXTURE)
        assert payload["incident_id"] == "inc-tel005-heartbeat-loss-001"
        assert payload["threshold_snapshot"]["metric_name"] == "heartbeat_lag_ms"
        assert payload["threshold_snapshot"]["breached"] is True
        assert payload["telemetry_event"]["event_type"] == "runtime.heartbeat_loss"
        assert payload["telemetry_event"]["heartbeat_lag_ms"] == 95000

    def test_replay_opens_incident(self):
        payload = _load(HEARTBEAT_LOSS_FIXTURE)
        r = client.post("/api/incidents/consume-threshold", json=payload)
        assert r.status_code == 201, r.text

        body = r.json()
        assert body["incident_id"] == "inc-tel005-heartbeat-loss-001"
        assert body["status"] == "open"
        assert body["severity"] == "critical"
        assert body["binding_id"] == "rtb-tel005-paper-001"
        assert "tel-tel005-heartbeat-loss-001" in body["telemetry_event_ids"]
        assert len(store.list_incidents()) == 1

    def test_replay_is_idempotent(self):
        payload = _load(HEARTBEAT_LOSS_FIXTURE)
        r1 = client.post("/api/incidents/consume-threshold", json=payload)
        r2 = client.post("/api/incidents/consume-threshold", json=payload)

        assert r1.status_code == 201
        assert r2.status_code == 200
        assert r2.json()["incident_id"] == r1.json()["incident_id"]
        assert len(store.list_incidents()) == 1

    def test_operator_payload_agrees_with_incident(self):
        payload = _load(HEARTBEAT_LOSS_FIXTURE)
        client.post("/api/incidents/consume-threshold", json=payload)
        incident_id = "inc-tel005-heartbeat-loss-001"

        inc_r = client.get(f"/api/incidents/{incident_id}")
        op_r = client.get(f"/api/incidents/{incident_id}/operator-payload")

        assert inc_r.status_code == 200
        assert op_r.status_code == 200

        inc = inc_r.json()
        op = op_r.json()

        assert op["incident_id"] == inc["incident_id"]
        assert op["status"] == inc["status"]
        assert op["severity"] == inc["severity"]
        assert op["binding_id"] == inc["binding_id"]
        assert op["runtime_id"] == inc["runtime_id"]
        assert op["telemetry_event_ids"] == inc["telemetry_event_ids"]
        assert op["is_open"] is True
        assert op["postmortem_id"] is None

    def test_evidence_summary_records_heartbeat_metric(self):
        payload = _load(HEARTBEAT_LOSS_FIXTURE)
        client.post("/api/incidents/consume-threshold", json=payload)
        r = client.get("/api/incidents/inc-tel005-heartbeat-loss-001")
        body = r.json()
        assert "heartbeat_lag_ms" in (body.get("evidence_summary") or "")


# ---------------------------------------------------------------------------
# Scenario 3: PnL drift → DriftReport → IncidentCase (via consume-drift-report)
# ---------------------------------------------------------------------------

class TestPnLDriftReplay:
    def _build_drift_report_from_fixture(self) -> dict:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_recon_consumer", _DRIFT_SERVICE_DIR / "consumer.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        event = json.loads(PNL_DRIFT_FIXTURE.read_text(encoding="utf-8"))
        return module.build_drift_report_from_event(event, existing_report_ids=set())

    def test_pnl_drift_event_produces_drift_report(self):
        report = self._build_drift_report_from_fixture()
        assert report is not None
        assert "pnl" in report["drift_type"] or report["metrics"]["worst_metric"] in {"pnl", "max_drawdown", "avg_slippage_bps"}
        assert report["severity"] in {"medium", "high", "critical"}
        assert report["status"] == "open"
        assert "evt-tel005-pnl-drift-001" in report["telemetry_event_ids"]
        assert report["binding_id"] == "rtb-tel005-paper-001"

    def test_drift_report_opens_incident_via_consume_endpoint(self):
        report = self._build_drift_report_from_fixture()
        r = client.post("/api/incidents/consume-drift-report", json={"drift_report": report})
        assert r.status_code == 201, r.text

        body = r.json()
        assert body["status"] == "open"
        assert body["binding_id"] == "rtb-tel005-paper-001"
        assert body["runtime_id"] == "runtime-tel005-001"
        assert "evt-tel005-pnl-drift-001" in body["telemetry_event_ids"]
        assert len(store.list_incidents()) == 1

    def test_drift_report_incident_operator_payload_agrees(self):
        report = self._build_drift_report_from_fixture()
        create_r = client.post("/api/incidents/consume-drift-report", json={"drift_report": report})
        assert create_r.status_code == 201
        incident_id = create_r.json()["incident_id"]

        inc_r = client.get(f"/api/incidents/{incident_id}")
        op_r = client.get(f"/api/incidents/{incident_id}/operator-payload")

        assert inc_r.status_code == 200
        assert op_r.status_code == 200

        inc = inc_r.json()
        op = op_r.json()

        assert op["incident_id"] == inc["incident_id"]
        assert op["status"] == inc["status"]
        assert op["binding_id"] == inc["binding_id"]
        assert op["telemetry_event_ids"] == inc["telemetry_event_ids"]
        assert op["reconciliation_ids"] == inc["reconciliation_ids"]
        assert op["is_open"] is True


# ---------------------------------------------------------------------------
# Scenario 4: Recovery — metrics within threshold → no drift report, no new incident
# ---------------------------------------------------------------------------

class TestRecoveryReplay:
    def _try_build_drift_report(self) -> dict | None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_recon_consumer_recovery", _DRIFT_SERVICE_DIR / "consumer.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        event = json.loads(RECOVERY_FIXTURE.read_text(encoding="utf-8"))
        return module.build_drift_report_from_event(event, existing_report_ids=set())

    def test_recovery_event_produces_no_drift_report(self):
        report = self._try_build_drift_report()
        assert report is None, (
            "Recovery event should produce no DriftReport because all metrics are within threshold. "
            f"Got: {report}"
        )

    def test_recovery_event_opens_no_incident(self):
        assert len(store.list_incidents()) == 0
        report = self._try_build_drift_report()
        assert report is None
        assert len(store.list_incidents()) == 0


# ---------------------------------------------------------------------------
# Cross-scenario: both spike incidents coexist and are listed correctly
# ---------------------------------------------------------------------------

class TestCrossScenarioProjection:
    def test_both_open_incidents_listed(self):
        order_payload = _load(ORDER_REJECTION_FIXTURE)
        hb_payload = _load(HEARTBEAT_LOSS_FIXTURE)

        r1 = client.post("/api/incidents/consume-threshold", json=order_payload)
        r2 = client.post("/api/incidents/consume-threshold", json=hb_payload)

        assert r1.status_code == 201
        assert r2.status_code == 201

        r = client.get("/api/incidents?open_only=true")
        assert r.status_code == 200
        ids = {i["incident_id"] for i in r.json()}
        assert "inc-tel005-order-rejection-spike-001" in ids
        assert "inc-tel005-heartbeat-loss-001" in ids

    def test_binding_filter_returns_both_incidents_for_same_binding(self):
        order_payload = _load(ORDER_REJECTION_FIXTURE)
        hb_payload = _load(HEARTBEAT_LOSS_FIXTURE)

        client.post("/api/incidents/consume-threshold", json=order_payload)
        client.post("/api/incidents/consume-threshold", json=hb_payload)

        r = client.get("/api/incidents?binding_id=rtb-tel005-paper-001")
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 2
        assert all(i["binding_id"] == "rtb-tel005-paper-001" for i in items)

    def test_operator_payloads_are_independent(self):
        order_payload = _load(ORDER_REJECTION_FIXTURE)
        hb_payload = _load(HEARTBEAT_LOSS_FIXTURE)

        client.post("/api/incidents/consume-threshold", json=order_payload)
        client.post("/api/incidents/consume-threshold", json=hb_payload)

        op_order = client.get("/api/incidents/inc-tel005-order-rejection-spike-001/operator-payload")
        op_hb = client.get("/api/incidents/inc-tel005-heartbeat-loss-001/operator-payload")

        assert op_order.status_code == 200
        assert op_hb.status_code == 200

        order_body = op_order.json()
        hb_body = op_hb.json()

        assert order_body["incident_id"] != hb_body["incident_id"]
        assert order_body["telemetry_event_ids"] != hb_body["telemetry_event_ids"]
        assert order_body["is_open"] is True
        assert hb_body["is_open"] is True
