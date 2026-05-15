"""
Contract tests for BFF-LUV-GAP-005: governance, deployment, runtime, risk,
incident, audit, and command-confirmation BFF compatibility surfaces.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore


OPERATOR_TOKEN = "Bearer op-gap-005:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


@contextmanager
def _isolated_bff() -> Iterator[tuple[TestClient, ReadSurfaceStore]]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        store = ReadSurfaceStore(
            os.path.join(td, "read_surfaces.json"),
            allow_local_snapshot_fallback=True,
        )
        bff_main.read_store = store
        bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._GOV_BFF_INCIDENT_OVERLAY.clear()
        try:
            yield TestClient(bff_main.app), store
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_INCIDENT_OVERLAY.clear()


def _assert_final_command_envelope(payload: dict, command: str) -> str:
    assert payload["status"] == "accepted"
    assert payload["data"]["command"] == command
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["receipt"]["status"] == "accepted"
    assert payload["data"]["routing_path"] == "direct"
    return payload["data"]["receipt_id"]


def test_bff_governance_review_routes_and_approval_evidence() -> None:
    with _isolated_bff() as (client, store):
        response = client.get("/bff/reviews", headers=HEADERS)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["items"][0]["item_id"] == "gov-review-001"
        assert payload["meta"]["surfaces"]["review_queue"]["status"] == "degraded"

        detail = client.get("/bff/reviews/gov-review-001", headers=HEADERS)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["item_type"] == "DeploymentPlan"

        validators = client.get("/bff/reviews/gov-review-001/validators", headers=HEADERS)
        assert validators.status_code == 200, validators.text
        assert validators.json()["review_id"] == "gov-review-001"
        assert "validators" in validators.json()

        audit = client.get("/bff/reviews/gov-review-001/audit", headers=HEADERS)
        assert audit.status_code == 200, audit.text
        assert audit.json()["events"][0]["entry_id"] == "audit-002"

        store.get_approval_decision = lambda approval_id: {
            "id": approval_id,
            "correlation_id": "corr-approval-005",
            "evidence_refs": [{"ref_id": "ev-005", "type": "IncidentReport", "url": None}],
        } if approval_id == "approval-gap-005" else None
        evidence = client.get("/bff/approvals/approval-gap-005/evidence", headers=HEADERS)
        assert evidence.status_code == 200, evidence.text
        body = evidence.json()
        assert body["correlation_id"] == "corr-approval-005"
        assert body["audit_ref"]["href"] == "/bff/audit/entities/ApprovalDecision/approval-gap-005"
        assert body["evidence"][0]["ref_id"] == "ev-005"


def test_bff_deployment_runtime_and_risk_action_routes_return_final_envelopes() -> None:
    with _isolated_bff() as (client, _store):
        deployments = client.get("/bff/deployments", headers=HEADERS)
        assert deployments.status_code == 200, deployments.text
        assert deployments.json()["items"][0]["plan_id"] == "plan-F-042"

        deployment = client.get("/bff/deployments/plan-F-042", headers=HEADERS)
        assert deployment.status_code == 200, deployment.text
        assert deployment.json()["data"]["plan_id"] == "plan-F-042"

        action_payload = {"reason": "execute-plans compatibility smoke"}
        first = client.post(
            "/bff/deployments/plan-F-042/actions/promote",
            json=action_payload,
            headers={**HEADERS, "Idempotency-Key": "gap-005-deployment-action"},
        )
        assert first.status_code == 202, first.text
        first_receipt = _assert_final_command_envelope(first.json(), "DeploymentAction")

        replay = client.post(
            "/bff/deployments/plan-F-042/actions/promote",
            json=action_payload,
            headers={**HEADERS, "Idempotency-Key": "gap-005-deployment-action"},
        )
        assert replay.status_code == 202, replay.text
        assert _assert_final_command_envelope(replay.json(), "DeploymentAction") == first_receipt

        conflict = client.post(
            "/bff/deployments/plan-F-042/actions/promote",
            json={"reason": "different payload"},
            headers={**HEADERS, "Idempotency-Key": "gap-005-deployment-action"},
        )
        assert conflict.status_code == 409, conflict.text

        runtimes = client.get("/bff/runtimes", headers=HEADERS)
        assert runtimes.status_code == 200, runtimes.text
        assert runtimes.json()["items"][0]["runtime_id"] == "runtime-042"

        runtime = client.get("/bff/runtimes/runtime-042", headers=HEADERS)
        assert runtime.status_code == 200, runtime.text
        assert runtime.json()["data"]["runtime_id"] == "runtime-042"

        runtime_action = client.post(
            "/bff/runtimes/runtime-042/actions/pause",
            json={"reason": "operator pause"},
            headers={**HEADERS, "Idempotency-Key": "gap-005-runtime-action"},
        )
        assert runtime_action.status_code == 202, runtime_action.text
        _assert_final_command_envelope(runtime_action.json(), "RuntimeAction")

        alerts = client.get("/bff/risk/alerts", headers=HEADERS)
        assert alerts.status_code == 200, alerts.text
        alert_id = alerts.json()["alerts"][0]["alert_id"]

        alert_detail = client.get(f"/bff/risk/alerts/{alert_id}", headers=HEADERS)
        assert alert_detail.status_code == 200, alert_detail.text
        assert alert_detail.json()["data"]["alert_id"] == alert_id

        alias = client.get("/bff/alerts", headers=HEADERS)
        assert alias.status_code == 200, alias.text
        assert alias.json()["alerts"][0]["alert_id"] == alert_id

        alert_action = client.post(
            f"/bff/risk/alerts/{alert_id}/actions/escalate",
            json={"reason": "risk owner review"},
            headers={**HEADERS, "Idempotency-Key": "gap-005-risk-alert-action"},
        )
        assert alert_action.status_code == 202, alert_action.text
        _assert_final_command_envelope(alert_action.json(), "RiskAlertAction")


def test_bff_incident_routes_support_create_detail_and_action() -> None:
    with _isolated_bff() as (client, _store):
        incidents = client.get("/bff/incidents", headers=HEADERS)
        assert incidents.status_code == 200, incidents.text
        assert incidents.json()["items"][0]["incident_id"] == "inc-20260410-001"

        existing = client.get("/bff/incidents/inc-20260410-001", headers=HEADERS)
        assert existing.status_code == 200, existing.text
        assert existing.json()["data"]["severity"] == "high"

        created = client.post(
            "/bff/incidents",
            json={
                "incident_id": "inc-gap-005",
                "title": "Execute-plans incident compatibility",
                "severity": "medium",
                "capital_pool_id": "pool-main",
            },
            headers={**HEADERS, "Idempotency-Key": "gap-005-create-incident"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["incident_id"] == "inc-gap-005"

        created_detail = client.get("/bff/incidents/inc-gap-005", headers=HEADERS)
        assert created_detail.status_code == 200, created_detail.text
        assert created_detail.json()["data"]["audit_ref"]["href"] == "/bff/audit/entities/Incident/inc-gap-005"

        action = client.post(
            "/bff/incidents/inc-gap-005/actions/resolve",
            json={"reason": "incident handled"},
            headers={**HEADERS, "Idempotency-Key": "gap-005-incident-action"},
        )
        assert action.status_code == 202, action.text
        _assert_final_command_envelope(action.json(), "IncidentAction")


def test_bff_audit_and_command_confirmation_routes() -> None:
    with _isolated_bff() as (client, _store):
        events = client.get(
            "/bff/audit/events",
            params={
                "actor": "operator-jane",
                "action_type": "ForwardToApprovalQueue",
                "target_type": "GovernanceReviewItem",
            },
            headers=HEADERS,
        )
        assert events.status_code == 200, events.text
        assert events.json()["events"][0]["entry_id"] == "audit-002"

        entity = client.get(
            "/bff/audit/entities/GovernanceReviewItem/gov-review-001",
            headers=HEADERS,
        )
        assert entity.status_code == 200, entity.text
        assert entity.json()["events"][0]["entry_id"] == "audit-002"

        export = client.get("/bff/audit/export", headers=HEADERS)
        assert export.status_code == 200, export.text
        assert export.json()["total"] >= 5

        missing_token = client.post(
            "/bff/command-confirmations",
            json={"command_id": "cmd-gap-005"},
            headers={**HEADERS, "Idempotency-Key": "gap-005-confirm-missing"},
        )
        assert missing_token.status_code == 400, missing_token.text
        assert missing_token.json()["detail"]["error"]["code"] == "CONFIRM_TOKEN_REQUIRED"

        confirmation = client.post(
            "/bff/command-confirmations",
            json={"command_id": "cmd-gap-005", "confirm_token": "confirm-gap-005"},
            headers={**HEADERS, "Idempotency-Key": "gap-005-confirm"},
        )
        assert confirmation.status_code == 202, confirmation.text
        body = confirmation.json()
        assert body["command_id"] == "cmd-gap-005"
        assert body["status"] == "accepted"
        assert body["confirmed_by"] == "op-gap-005"

        replay = client.post(
            "/bff/command-confirmations",
            json={"command_id": "cmd-gap-005", "confirm_token": "confirm-gap-005"},
            headers={**HEADERS, "Idempotency-Key": "gap-005-confirm"},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["confirmation_id"] == body["confirmation_id"]
