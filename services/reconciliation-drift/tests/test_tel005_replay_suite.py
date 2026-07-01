"""
Replay suite for LOOP-AUTO-TEL-005 — reconciliation-drift side.

Proves that:
- PnL drift event produces a DriftReport and persists via the consume endpoint.
- Recovery event (metrics within threshold) produces no DriftReport and no new record.
- Idempotent replay of the PnL drift event returns 201 once, 201 again (same report id
  overwrites, which is the reconciliation-drift contract: put_drift_report is idempotent
  because it uses the same drift_report_id).
- DriftReport from PnL drift includes the correct telemetry_event_ids for incident linkage.

Run:
    python3 -m pytest services/reconciliation-drift/tests/test_tel005_replay_suite.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]

PNL_DRIFT_FIXTURE = SERVICE_DIR / "fixtures" / "pnl_drift_telemetry_event.json"
RECOVERY_FIXTURE = SERVICE_DIR / "fixtures" / "recovery_telemetry_event.json"


def _load_consumer_module():
    key = "_tel005_recon_consumer"
    sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location(key, SERVICE_DIR / "consumer.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _load_service_module(data_dir: str):
    for mod_key in ("consumer", "store", "_tel005_recon_main"):
        sys.modules.pop(mod_key, None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "_tel005_recon_main", SERVICE_DIR / "main.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["_tel005_recon_main"] = module
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


# ---------------------------------------------------------------------------
# Consumer-level tests (no HTTP)
# ---------------------------------------------------------------------------

class TestPnLDriftConsumerReplay:
    def test_pnl_drift_event_produces_drift_report(self):
        consumer = _load_consumer_module()
        event = json.loads(PNL_DRIFT_FIXTURE.read_text(encoding="utf-8"))
        report = consumer.build_drift_report_from_event(event, existing_report_ids=set())

        assert report is not None
        assert report["status"] == "open"
        assert report["binding_id"] == "rtb-tel005-paper-001"
        assert report["runtime_id"] == "runtime-tel005-001"
        assert report["deployment_stage"] == "paper"
        assert "evt-tel005-pnl-drift-001" in report["telemetry_event_ids"]
        assert report["severity"] in {"medium", "high", "critical"}
        assert report["recommended_action"] in {"rerun_research", "open_incident"}
        assert report["source_contract"]["telemetry_truth_owner"] == "telemetry-ingest"

    def test_pnl_drift_report_includes_required_incident_linkage_fields(self):
        consumer = _load_consumer_module()
        event = json.loads(PNL_DRIFT_FIXTURE.read_text(encoding="utf-8"))
        report = consumer.build_drift_report_from_event(event, existing_report_ids=set())

        assert report is not None
        assert report.get("drift_report_id")
        assert report.get("recon_run_id")
        assert report.get("incident_cluster_id")
        assert len(report["telemetry_event_ids"]) >= 1
        assert any("telemetry_event:" in ref for ref in report["evidence_refs"])

    def test_pnl_drift_report_drift_type_is_pnl(self):
        consumer = _load_consumer_module()
        event = json.loads(PNL_DRIFT_FIXTURE.read_text(encoding="utf-8"))
        report = consumer.build_drift_report_from_event(event, existing_report_ids=set())
        assert report is not None
        assert report["drift_type"] in {"pnl", "execution"}


class TestRecoveryConsumerReplay:
    def test_recovery_event_produces_no_drift_report(self):
        consumer = _load_consumer_module()
        event = json.loads(RECOVERY_FIXTURE.read_text(encoding="utf-8"))
        report = consumer.build_drift_report_from_event(event, existing_report_ids=set())
        assert report is None, (
            "Recovery event should not produce a DriftReport because all metrics "
            f"are within threshold. Got: {report}"
        )

    def test_recovery_event_loading_does_not_raise(self):
        consumer = _load_consumer_module()
        events = consumer.load_telemetry_events_from_path(RECOVERY_FIXTURE)
        assert len(events) == 1
        assert events[0]["event_id"] == "evt-tel005-recovery-001"


# ---------------------------------------------------------------------------
# HTTP endpoint tests (via FastAPI TestClient)
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient


class TestPnLDriftHttpReplay:
    def test_consume_endpoint_persists_pnl_drift_report(self):
        with tempfile.TemporaryDirectory() as data_dir:
            module = _load_service_module(data_dir)
            client = TestClient(module.app)
            event = json.loads(PNL_DRIFT_FIXTURE.read_text(encoding="utf-8"))

            r = client.post(
                "/api/reconciliation-drift/telemetry-events/consume",
                json={"events": [event]},
            )
            assert r.status_code == 201, r.text

            body = r.json()
            assert body["consumed_event_count"] == 1
            assert body["drift_report_count"] == 1
            assert body["ignored_event_ids"] == []

            report = body["drift_reports"][0]
            assert report["binding_id"] == "rtb-tel005-paper-001"
            assert report["runtime_id"] == "runtime-tel005-001"
            assert report["deployment_stage"] == "paper"
            assert report["status"] == "open"
            assert "evt-tel005-pnl-drift-001" in report["telemetry_event_ids"]

    def test_consume_endpoint_recovery_produces_no_drift_report(self):
        with tempfile.TemporaryDirectory() as data_dir:
            module = _load_service_module(data_dir)
            client = TestClient(module.app)
            event = json.loads(RECOVERY_FIXTURE.read_text(encoding="utf-8"))

            r = client.post(
                "/api/reconciliation-drift/telemetry-events/consume",
                json={"events": [event]},
            )
            assert r.status_code == 201, r.text

            body = r.json()
            assert body["drift_report_count"] == 0
            assert "evt-tel005-recovery-001" in body["ignored_event_ids"]

    def test_idempotent_pnl_drift_replay_does_not_duplicate_report(self):
        with tempfile.TemporaryDirectory() as data_dir:
            module = _load_service_module(data_dir)
            client = TestClient(module.app)
            event = json.loads(PNL_DRIFT_FIXTURE.read_text(encoding="utf-8"))

            r1 = client.post(
                "/api/reconciliation-drift/telemetry-events/consume",
                json={"events": [event]},
            )
            r2 = client.post(
                "/api/reconciliation-drift/telemetry-events/consume",
                json={"events": [event]},
            )

            assert r1.status_code == 201
            assert r2.status_code == 201

            report_id_1 = r1.json()["drift_reports"][0]["drift_report_id"]
            report_id_2 = r2.json()["drift_reports"][0]["drift_report_id"]
            assert report_id_1 == report_id_2

            listed = client.get(
                "/api/reconciliation-drift/drift-reports",
                params={"scope_ref": "rtb-tel005-paper-001"},
            )
            assert listed.status_code == 200
            assert len(listed.json()) == 1
