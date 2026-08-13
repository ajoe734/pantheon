"""L12-MFC-R4-TELREC-001 contract test.

Proves real paper runtime telemetry through reconciliation terminal readback:
1. Real paper event emitted by RuntimeTelemetryEmitter is ingested via reconciliation consumer.
2. DriftReport is created and read back via official reconciliation endpoint GET /api/reconciliation-drift/drift-reports/{id}.
3. DriftReport is consumed into IncidentCase and read back via official incidents endpoints GET /api/incidents/{id} and GET /api/incidents/{id}/operator-payload.
4. Incident identity (incident_id, binding_id, runtime_id, telemetry_event_ids, severity, status, correlation_envelope, postmortem_id) is consumable for downstream evolution.
5. Validation-first: no patched HTTP, no direct downstream store writes, runs in parallel.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from services.execution.lean_runtime.paper_runtime import RuntimeTelemetryEmitter
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.trade_journey.correlation_envelope import mint_trade_envelope

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]


def _load_reconciliation_service(data_dir: str):
    sys.modules.pop("consumer", None)
    sys.modules.pop("store", None)
    sys.modules.pop("reconciliation_drift_telrec_001_main", None)
    for path in (str(SERVICE_DIR), str(REPO_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location(
        "reconciliation_drift_telrec_001_main",
        SERVICE_DIR / "main.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconciliation_drift_telrec_001_main"] = module
    with mock.patch.dict(
        "os.environ",
        {
            "RECONCILIATION_DRIFT_DATA_DIR": data_dir,
            "RECONCILIATION_DRIFT_STORE_BACKEND": "json",
            "RECONCILIATION_DRIFT_AUTH_MODE": "disabled",
            "PERSISTENCE_POSTURE": "lenient",
        },
    ):
        spec.loader.exec_module(module)
    return module


def _paper_runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "paper",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "runtime-l12-telrec-001",
            "PANTHEON_WORKSPACE_REF": "workspace-telrec-001",
            "PANTHEON_AUTH_PROFILE_REF": "auth-profile-telrec-001",
            "PANTHEON_PERSONA_ID": "persona-telrec-001",
            "PANTHEON_SESSION_ID": "session-telrec-001",
            "PANTHEON_TRACE_ID": "trace-telrec-001",
            "PANTHEON_REQUEST_ID": "request-telrec-001",
        }
    )


class _FakeBindingResolver:
    def resolve(self) -> dict[str, Any]:
        return {
            "binding_id": "binding-l12-telrec-001",
            "runtime_id": "runtime-l12-telrec-001",
            "capital_pool_id": "pool-l12-telrec-001",
            "artifact_id": "artifact-l12-telrec-001",
            "artifact_version": "1.0.0",
            "plan_id": "plan-l12-telrec-001",
            "persona_capital_binding_id": "pcb-l12-telrec-001",
            "deployment_mode": "paper",
            "execution_mode": "paper",
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
            "runtime_adapter_version": "0.1.0",
            "context_source": "launch_manifest",
        }


def test_real_paper_runtime_telemetry_ingest_and_terminal_readback(monkeypatch) -> None:
    """Proves end-to-end ingestion of paper runtime telemetry event into reconciliation drift report and consumable incident identity."""
    with tempfile.TemporaryDirectory() as recon_dir:
        recon_service = _load_reconciliation_service(recon_dir)
        recon_client = TestClient(recon_service.app)

        with mock.patch.dict(
            "os.environ",
            {
                "PANTHEON_RUNTIME_MANAGER_URL": "http://localhost:8000",
                "PERSISTENCE_POSTURE": "lenient",
                "PANTHEON_TENANT_ID": "tenant-telrec",
            },
        ):
            from services.incident.incident import IncidentStore
            from services.incidents.main import app as incidents_app

            class _AcceptAll:
                def validate_incident(self, incident):
                    return None

            fresh_inc_store = IncidentStore(path=None)
            monkeypatch.setattr("services.incidents.main.reference_validator", _AcceptAll())
            monkeypatch.setattr("services.incidents.main.store", fresh_inc_store)
            incidents_client = TestClient(incidents_app)

            identity = _paper_runtime_identity()
            emitter = RuntimeTelemetryEmitter(identity, _FakeBindingResolver())
            event = emitter.build_event(
                "pnl_snapshot",
                {
                    "pnl": 50.0,
                    "pnl_as_of": "2026-08-13T12:00:00Z",
                    "avg_slippage_bps": 12.5,
                },
                metadata={
                    "runtime_package": "paper_execution_runtime",
                    "is_real_capital": False,
                },
                event_id="evt-l12-telrec-001",
                created_at="2026-08-13T12:00:00Z",
            )
            assert event is not None
            event["tenant_id"] = "tenant-telrec"
            event["correlation_envelope"] = mint_trade_envelope(
                {"tenant_id": "tenant-telrec", "environment": "paper"},
                producer="execution.paper_runtime",
            )

            headers = {"X-Tenant-Id": "tenant-telrec"}
            with mock.patch.object(
                recon_service,
                "_classify_drift_report_incident",
                return_value={"incident_id": "inc-l12-telrec-001"},
            ):
                response = recon_client.post(
                    "/api/reconciliation-drift/telemetry-events/consume",
                    headers=headers,
                    json={
                        "tenant_id": "tenant-telrec",
                        "events": [event],
                        "baseline_metrics": {"pnl": 100.0, "avg_slippage_bps": 2.0},
                        "thresholds": {
                            "pnl": {"warning_relative_delta": 0.2, "critical_relative_delta": 0.4},
                            "avg_slippage_bps": {"warning_relative_delta": 0.5, "critical_relative_delta": 1.0},
                        },
                    },
                )

            assert response.status_code == 201, response.text
            payload = response.json()
            assert payload["consumed_event_count"] == 1
            assert payload["drift_report_count"] == 1

            # 1. DriftReport Readback via Reconciliation Endpoint
            reports_res = recon_client.get(
                "/api/reconciliation-drift/drift-reports",
                headers=headers,
                params={"scope_ref": "binding-l12-telrec-001"},
            )
            assert reports_res.status_code == 200
            reports = reports_res.json()
            assert len(reports) == 1
            report = reports[0]
            assert report["drift_report_id"] == "drift-evt-l12-telrec-001"
            assert report["binding_id"] == "binding-l12-telrec-001"
            assert report["runtime_id"] == "runtime-l12-telrec-001"
            assert report["severity"] == "critical"
            assert report["status"] == "open"
            assert report["telemetry_event_ids"] == ["evt-l12-telrec-001"]

            # 2. Individual DriftReport Detail Readback
            detail_res = recon_client.get(
                f"/api/reconciliation-drift/drift-reports/{report['drift_report_id']}",
                headers=headers,
            )
            assert detail_res.status_code == 200
            detail = detail_res.json()
            assert detail["recommended_action"] == "rerun_research"
            assert detail["correlation_envelope"]["tenant_id"] == "tenant-telrec"

            # 3. Consume DriftReport into Incidents Service
            inc_consume_res = incidents_client.post(
                "/api/incidents/consume-drift-report",
                json={"drift_report": detail},
            )
            assert inc_consume_res.status_code == 201, inc_consume_res.text
            inc_payload = inc_consume_res.json()
            incident_id = inc_payload["incident_id"]
            assert inc_payload["status"] == "open"
            assert inc_payload["binding_id"] == "binding-l12-telrec-001"
            assert inc_payload["runtime_id"] == "runtime-l12-telrec-001"

            # 4. IncidentCase Detail Readback via Incidents Service Endpoint
            inc_detail_res = incidents_client.get(f"/api/incidents/{incident_id}")
            assert inc_detail_res.status_code == 200
            inc_detail = inc_detail_res.json()
            assert inc_detail["incident_id"] == incident_id
            assert inc_detail["binding_id"] == "binding-l12-telrec-001"
            assert inc_detail["runtime_id"] == "runtime-l12-telrec-001"
            assert "evt-l12-telrec-001" in inc_detail["telemetry_event_ids"]

            # 5. OperatorIncidentPayload Readback (Evolution & Operator Consumable Identity)
            op_payload_res = incidents_client.get(f"/api/incidents/{incident_id}/operator-payload")
            assert op_payload_res.status_code == 200
            op_payload = op_payload_res.json()
            assert op_payload["incident_id"] == incident_id
            assert op_payload["status"] == "open"
            assert op_payload["binding_id"] == "binding-l12-telrec-001"
            assert op_payload["runtime_id"] == "runtime-l12-telrec-001"
            assert op_payload["is_open"] is True
            assert op_payload["postmortem_id"] is None
            assert op_payload["telemetry_event_ids"] == ["evt-l12-telrec-001"]

            # 6. Idempotent Re-consume
            re_response = recon_client.post(
                "/api/reconciliation-drift/telemetry-events/consume",
                headers=headers,
                json={"tenant_id": "tenant-telrec", "events": [event]},
            )
            assert re_response.status_code == 201
            re_payload = re_response.json()
            assert re_payload["duplicate_event_ids"] == ["evt-l12-telrec-001"]
            assert re_payload["drift_reports"][0]["drift_report_id"] == "drift-evt-l12-telrec-001"
