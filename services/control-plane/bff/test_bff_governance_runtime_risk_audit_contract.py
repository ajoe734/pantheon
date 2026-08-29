"""
Contract tests for BFF-LUV-GAP-005: governance, deployment, runtime, risk,
incident, audit, and command-confirmation BFF compatibility surfaces.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from command_queue import CommandStore
from ports import ReadSurfacePorts


OPERATOR_TOKEN = "Bearer op-gap-005:operator"
HEADERS = {"Authorization": OPERATOR_TOKEN}


class GovernanceRuntimeRiskAuditTestReadPorts(ReadSurfacePorts):
    def __init__(self, seed_data: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._data: dict[str, Any] = seed_data or {
            "review_queue": [
                {
                    "item_id": "gov-review-001",
                    "item_type": "DeploymentPlan",
                    "status": "pending",
                    "title": "Gov review 001",
                    "review_summary": {
                        "validators": [{"validator_id": "val-1", "name": "RiskValidator"}]
                    },
                }
            ],
            "deployment_plans": [
                {
                    "plan_id": "plan-F-042",
                    "id": "plan-F-042",
                    "name": "Deployment Plan F-042",
                    "status": "ready",
                }
            ],
            "runtime_bindings": [
                {
                    "runtime_id": "runtime-042",
                    "id": "runtime-042",
                    "name": "Runtime 042",
                    "status": "active",
                }
            ],
            "alerts": [
                {
                    "alert_id": "alert-001",
                    "id": "alert-001",
                    "title": "Risk alert 001",
                    "severity": "medium",
                }
            ],
            "incidents": [
                {
                    "incident_id": "inc-20260410-001",
                    "id": "inc-20260410-001",
                    "title": "Incident 001",
                    "severity": "high",
                    "status": "investigating",
                }
            ],
            "audit_log": [
                {
                    "entry_id": f"audit-00{i}",
                    "id": f"audit-00{i}",
                    "actor": "operator-jane",
                    "action_type": "ForwardToApprovalQueue",
                    "target_type": "GovernanceReviewItem",
                    "target_id": "gov-review-001",
                    "timestamp": f"2026-08-29T00:00:0{9 if i == 2 else i}Z",
                }
                for i in (2, 1, 3, 4, 5)
            ],
        }

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "degraded" if dataset == "review_queue" or dataset == "governance_review_queue_items" else "ok",
            "source": "local_snapshot",
            "snapshot_at": snapshot_at,
        }

    def _get_dataset(self, name: str) -> dict[str, Any] | list[Any]:
        return self._data.setdefault(name, [])

    def list_governance_review_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("review_queue")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_review_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_governance_review_queue_items(**kwargs)

    def get_review_queue_item(self, item_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("review_queue")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        return next((i for i in items if i.get("item_id") == item_id or i.get("id") == item_id), None)

    def list_validators_for_review(self, item_id: str | None) -> list[dict[str, Any]]:
        item = self.get_review_queue_item(item_id)
        if not item:
            return []
        review_summary = item.get("review_summary") or {}
        return list(review_summary.get("validators") or item.get("validators", []))

    def get_audit_events_for_review(self, item_id: str | None) -> list[dict[str, Any]]:
        events = self.list_governance_audit_events()
        return [e for e in events if e.get("target_id") == item_id or e.get("review_id") == item_id]

    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("deployment_plans")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_deployment_plan(self, plan_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("deployment_plans")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        return next((p for p in items if p.get("plan_id") == plan_id or p.get("id") == plan_id), None)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("runtime_bindings")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_runtimes(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_runtime_bindings(**kwargs)

    def get_runtime_binding(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("runtime_bindings")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        return next((r for r in items if r.get("runtime_id") == runtime_id or r.get("id") == runtime_id), None)

    def get_runtime(self, runtime_id: str | None) -> dict[str, Any] | None:
        return self.get_runtime_binding(runtime_id)

    def list_alerts(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("alerts")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_alert(self, alert_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("alerts")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        return next((a for a in items if a.get("alert_id") == alert_id or a.get("id") == alert_id), None)

    def list_incidents(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("incidents")
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_incident(self, incident_id: str | None) -> dict[str, Any] | None:
        ds = self._get_dataset("incidents")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        return next((i for i in items if i.get("incident_id") == incident_id or i.get("id") == incident_id), None)

    def list_governance_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._get_dataset("audit_log")
        items = list(ds.values()) if isinstance(ds, dict) else list(ds)
        actor = kwargs.get("actor")
        action_types = kwargs.get("action_types") or kwargs.get("action_type")
        target_type = kwargs.get("target_type")
        target_id = kwargs.get("target_id")
        if actor:
            items = [i for i in items if i.get("actor") == actor]
        if action_types:
            if isinstance(action_types, str):
                action_types = [a.strip() for a in action_types.split(",")]
            items = [i for i in items if i.get("action_type") in action_types]
        if target_type:
            items = [i for i in items if i.get("target_type") == target_type]
        if target_id:
            items = [i for i in items if i.get("target_id") == target_id]
        return items

    def list_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_governance_audit_events(**kwargs)

    def export_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.list_governance_audit_events(**kwargs)


@contextmanager
def _isolated_bff() -> Iterator[tuple[TestClient, GovernanceRuntimeRiskAuditTestReadPorts]]:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        store = GovernanceRuntimeRiskAuditTestReadPorts()
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
        if first.status_code == 202:
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
        else:
            assert first.status_code == 410

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
        if runtime_action.status_code == 202:
            _assert_final_command_envelope(runtime_action.json(), "RuntimeAction")
        else:
            assert runtime_action.status_code == 410

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
        if alert_action.status_code == 202:
            _assert_final_command_envelope(alert_action.json(), "RiskAlertAction")
        else:
            assert alert_action.status_code == 410


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
        if action.status_code == 202:
            _assert_final_command_envelope(action.json(), "IncidentAction")
        else:
            assert action.status_code == 410


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
        assert missing_token.json()["error"]["code"] == "CONFIRMATION_REQUIRED"

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
