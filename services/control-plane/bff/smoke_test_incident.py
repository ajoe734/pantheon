#!/usr/bin/env python3
"""Smoke test for APP-002-W2-READ-INCIDENT: Incident Response read surfaces."""
from __future__ import annotations

import sys
import os
import json
import tempfile
from pathlib import Path
from unittest import mock

# Ensure the BFF module is importable
sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient

_FIXTURE_TMP = tempfile.TemporaryDirectory(prefix="pantheon-bff-incident-smoke-")
_FIXTURE_ROOT = Path(_FIXTURE_TMP.name)
_INCIDENT_SERVICE_DIR = _FIXTURE_ROOT / "incidents"
_BFF_DATA_DIR = _FIXTURE_ROOT / "bff"
_INCIDENT_STORE_PATH = _INCIDENT_SERVICE_DIR / "incidents.json"


incident_live = {
    "incident_id": "inc-20260410-001",
    "title": "Unexpected drawdown in persona-alpha",
    "severity": "high",
    "status": "open",
    "created_at": "2026-04-10T14:30:00Z",
    "binding_id": "runtime-042",
    "deployment_stage": "live",
    "deployment_plan_id": "plan-F-042",
    "capital_pool_id": "pool-main",
    "persona_capital_binding_id": "binding-042",
    "artifact_id": "artifact-042",
    "artifact_version": "v2.1.0",
    "runtime_id": "runtime-042",
    "trace_id": "trace-inc-20260410-001",
    "telemetry_event_ids": ["tl-001"],
    "evidence_summary": "12% drawdown exceeded 10% threshold; runtime paused pending review.",
    "lineage_ref": "artifact-042@v2.1.0",
}
incident_resolved = {
    "incident_id": "inc-20260409-002",
    "title": "Deployment plan plan-F-042 stalled at paper stage",
    "severity": "medium",
    "status": "resolved",
    "created_at": "2026-04-09T08:00:00Z",
    "resolved_at": "2026-04-09T10:30:00Z",
    "binding_id": "runtime-042",
    "deployment_stage": "paper",
    "deployment_plan_id": "plan-F-042",
    "capital_pool_id": "pool-main",
    "persona_capital_binding_id": "binding-042",
    "artifact_id": "artifact-042",
    "artifact_version": "v2.1.0",
    "runtime_id": "runtime-042",
    "trace_id": "trace-inc-20260409-002",
    "telemetry_event_ids": [],
    "evidence_summary": "Promotion gate timeout during artifact validation.",
    "lineage_ref": "artifact-042@v2.1.0",
}
postmortem = {
    "postmortem_id": "pm-20260409-002",
    "incident_id": "inc-20260409-002",
    "title": "Postmortem: Deployment plan F-042 promotion timeout",
    "status": "published",
    "created_at": "2026-04-09T11:00:00Z",
    "published_at": "2026-04-09T12:00:00Z",
    "binding_id": "runtime-042",
    "deployment_stage": "paper",
    "deployment_plan_id": "plan-F-042",
    "capital_pool_id": "pool-main",
    "persona_capital_binding_id": "binding-042",
    "artifact_id": "artifact-042",
    "artifact_version": "v2.1.0",
    "runtime_id": "runtime-042",
    "trace_id": "trace-inc-20260409-002",
    "root_cause": "Promotion gate timeout was set too low for artifact validation under load.",
    "contributing_factors": [
        "Artifact validation queue became saturated during peak load",
        "Timeout threshold was insufficient for large artifact bundles",
    ],
    "timeline": [
        {"at": "2026-04-09T08:00:00Z", "event": "Incident opened"},
        {"at": "2026-04-09T10:30:00Z", "event": "Incident resolved"},
        {"at": "2026-04-09T11:00:00Z", "event": "Postmortem drafted"},
    ],
    "action_items": [
        "Increase promotion gate timeout to 120s",
        "Add queue-depth alerting for promotion gate",
    ],
    "author_ids": ["platform"],
}


def _seed_incident_service_store() -> None:
    _INCIDENT_SERVICE_DIR.mkdir(parents=True, exist_ok=True)
    _BFF_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _INCIDENT_STORE_PATH.write_text(
        json.dumps(
            {
                "incidents": [incident_live, incident_resolved],
                "postmortems": [postmortem],
            },
            indent=2,
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )


_seed_incident_service_store()
os.environ["BFF_DATA_DIR"] = str(_BFF_DATA_DIR)
os.environ["INCIDENTS_DATA_DIR"] = str(_INCIDENT_SERVICE_DIR)
os.environ["POSTMORTEMS_DATA_DIR"] = str(_INCIDENT_SERVICE_DIR)
os.environ["PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK"] = "false"
os.environ["BFF_READ_SURFACE_STATE"] = "fresh"
os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")
os.environ.setdefault("PANTHEON_BFF_AUTH_MODE", "permissive")
for _env_name in (
    "PANTHEON_BFF_INCIDENT_STORE",
    "PANTHEON_BFF_POSTMORTEM_STORE",
    "PANTHEON_INCIDENTS_API_URL",
    "PANTHEON_INCIDENTS_URL",
    "PANTHEON_POSTMORTEMS_API_URL",
    "PANTHEON_POSTMORTEMS_URL",
):
    os.environ.pop(_env_name, None)

import main as bff_main
from main import app
from ports import ReadSurfacePorts


class IncidentSmokeStore(ReadSurfacePorts):
    def __init__(self) -> None:
        super().__init__()
        self._incidents = {
            "inc-20260410-001": incident_live,
            "inc-20260409-002": incident_resolved,
        }
        self._postmortems = {
            "pm-20260409-002": postmortem,
        }

    def dataset_source(self, dataset: str) -> str:
        return "incident_service"

    def list_incidents(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        incidents = list(self._incidents.values())
        if status:
            statuses = {s.strip().lower() for s in status.split(",") if s.strip()}
            incidents = [i for i in incidents if str(i.get("status") or "").lower() in statuses]
        if severity:
            incidents = [i for i in incidents if i.get("severity") == severity]
        if affected_pool_id:
            incidents = [i for i in incidents if i.get("capital_pool_id") == affected_pool_id]
        anchor = [i for i in incidents if i.get("incident_id") == "inc-20260410-001"]
        rest = [i for i in incidents if i.get("incident_id") != "inc-20260410-001"]
        return anchor + sorted(rest, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    def get_incident(self, incident_id: str) -> Optional[dict[str, Any]]:
        return self._incidents.get(incident_id)

    def list_postmortems(self, time_range: Optional[str] = None) -> list[dict[str, Any]]:
        return list(self._postmortems.values())

    def get_postmortem(self, postmortem_id: str) -> Optional[dict[str, Any]]:
        return self._postmortems.get(postmortem_id)

    def get_postmortem_for_incident(self, incident_id: str) -> Optional[dict[str, Any]]:
        for pm in self._postmortems.values():
            if pm.get("incident_id") == incident_id:
                return pm
        return None

    def get_evolution_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        return None

    def list_evolution_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_lineage_edges(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_telemetry_performance(self, artifact_id: str) -> Optional[dict[str, Any]]:
        return {"artifact_id": artifact_id, "metrics": {}}

    def get_binding(self, binding_id: str) -> Optional[dict[str, Any]]:
        return None

    def get_runtime_binding(self, binding_id: str) -> Optional[dict[str, Any]]:
        return None

    def get_capital_pool(self, pool_id: str) -> Optional[dict[str, Any]]:
        return None

    def get_kill_switch_status(self) -> dict[str, Any]:
        return {
            "status": "operational",
            "active_scopes": [],
            "last_updated_at": "2026-04-10T14:30:00Z",
            "allowedActions": ["pause", "resume", "liquidate"],
        }


class UnavailableIncidentStore(ReadSurfacePorts):
    def dataset_source(self, dataset: str) -> str:
        return "missing"

    def list_incidents(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def get_incident(self, incident_id: str) -> Optional[dict[str, Any]]:
        return None


bff_main.read_store = IncidentSmokeStore()

client = TestClient(app)

AUTH = "Bearer test-operator:operator,admin"
ADMIN_MFA_AUTH = "Bearer test-admin:admin:mfa"


def _incident_command_payload(command: str, params: dict) -> dict:
    return {
        "command": command,
        "target": {"type": "Runtime", "id": "runtime-042"},
        "params": params,
        "audit_context": {
            "reason": f"{command} test rationale",
            "timestamp": "2026-04-17T15:10:00Z",
            "incident_id": "inc-20260410-001",
        },
    }


def _command_headers(auth: str, key: str) -> dict:
    return {
        "Authorization": auth,
        "X-Idempotency-Key": key,
    }


def test_in01_incident_list():
    resp = client.get("/api/v1/incidents", headers={"Authorization": AUTH})
    assert resp.status_code == 200, f"IN-01 failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert "items" in body
    assert "page_info" in body
    assert "meta" in body
    assert len(body["items"]) >= 1
    assert body["page_info"]["next_page_token"] in (None, "", "20")
    assert "snapshot_at" in body["meta"]
    assert "incident_list" in body["meta"]["surfaces"]
    assert body["meta"]["surfaces"]["incident_list"]["status"] in ("ok", "degraded", "unavailable")

    item = body["items"][0]
    for field in ("incident_id", "title", "severity", "status", "artifact_id", "opened_at"):
        assert field in item, f"Missing incident field: {field}"
    assert item["severity"] in ("sev1", "sev2", "sev3")
    print("✅ IN-01: Incident List")


def test_in01_incident_list_filtered():
    resp = client.get("/api/v1/incidents?status=open,in_progress", headers={"Authorization": AUTH})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) >= 1
    assert all(i["status"] in ("open", "in_progress") for i in body["items"])
    print("✅ IN-01: Incident List (filtered by comma-separated status)")


def test_in01_resolved_incident_list_includes_resolved_at():
    resp = client.get("/api/v1/incidents?status=resolved", headers={"Authorization": AUTH})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) >= 1
    assert all(i["status"] == "resolved" for i in body["items"])
    for item in body["items"]:
        assert "resolved_at" in item, "Missing incident field: resolved_at"
        assert item["resolved_at"], "resolved incidents must expose resolved_at"
    print("✅ PKT-003: Resolved incident list exposes resolved_at")


def test_in02_incident_detail():
    resp = client.get("/api/v1/incidents/inc-20260410-001", headers={"Authorization": AUTH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["incident_id"] == "inc-20260410-001"
    print("✅ IN-02: Incident Detail")


def test_in02_incident_detail_not_found():
    resp = client.get("/api/v1/incidents/inc-nonexistent", headers={"Authorization": AUTH})
    assert resp.status_code == 404
    print("✅ IN-02: Incident Detail (404 for nonexistent)")


def test_in02_missing_backend_does_not_fabricate_incidents():
    unavailable_store = UnavailableIncidentStore()
    original_read_store = bff_main.read_store
    with mock.patch.dict(
        os.environ,
        {
            "INCIDENTS_DATA_DIR": "",
            "POSTMORTEMS_DATA_DIR": "",
            "PANTHEON_BFF_INCIDENT_STORE": "",
            "PANTHEON_BFF_POSTMORTEM_STORE": "",
            "PANTHEON_INCIDENTS_API_URL": "",
            "PANTHEON_INCIDENTS_URL": "",
            "PANTHEON_POSTMORTEMS_API_URL": "",
            "PANTHEON_POSTMORTEMS_URL": "",
        },
        clear=False,
    ):
        bff_main.read_store = unavailable_store
        try:
            list_resp = client.get("/api/v1/incidents", headers={"Authorization": AUTH})
            detail_resp = client.get("/api/v1/incidents/inc-20260410-001", headers={"Authorization": AUTH})
        finally:
            bff_main.read_store = original_read_store

    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body["items"] == []
    assert list_body["meta"]["surfaces"]["incident_list"]["status"] == "unavailable"
    assert list_body["meta"]["surfaces"]["incident_list"]["source"] == "missing"
    assert list_body["meta"]["degradation"]["reason"]
    assert detail_resp.status_code == 404
    print("✅ IN-02: Missing backend does not fabricate incident records")


def test_in03_postmortem_list():
    resp = client.get("/api/v1/postmortems", headers={"Authorization": AUTH})
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    print("✅ IN-03: Postmortem List")


def test_in04_postmortem_detail():
    resp = client.get("/api/v1/postmortems/pm-20260409-002", headers={"Authorization": AUTH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["postmortem_id"] == "pm-20260409-002"
    assert "linked_incident" in body["data"]
    print("✅ IN-04: Postmortem Detail (with linked incident)")


def test_in04_postmortem_not_found():
    resp = client.get("/api/v1/postmortems/pm-nonexistent", headers={"Authorization": AUTH})
    assert resp.status_code == 404
    print("✅ IN-04: Postmortem Detail (404 for nonexistent)")


def test_in05_kill_switch_status():
    resp = client.get("/api/v1/kill-switch/status", headers={"Authorization": AUTH})
    assert resp.status_code == 200
    body = resp.json()
    assert "kill_switch" in body
    assert "allowedActions" in body
    assert "meta" in body
    assert "snapshot_at" in body["meta"]
    assert "kill_switch" in body["meta"]["surfaces"]
    assert "allowedActions" in body["meta"]["surfaces"]
    assert body["meta"]["surfaces"]["kill_switch"]["status"] in ("ok", "degraded", "unavailable")
    assert body["meta"]["surfaces"]["allowedActions"]["status"] in ("ok", "degraded", "unavailable")
    assert "status" in body["kill_switch"]
    assert "last_triggered_at" in body["kill_switch"]
    assert "last_confirmed_at" in body["kill_switch"]
    assert "active_commands" in body["kill_switch"]
    assert body["kill_switch"]["status"] in ("armed", "triggered", "cooling_down", None)
    for field in (
        "canPause",
        "canRiskOff",
        "canLiquidateAll",
        "canHardRollback",
        "canIssueSafeMode",
        "secondaryPathAvailable",
    ):
        assert field in body["allowedActions"]
        assert isinstance(body["allowedActions"][field], bool)
    print("✅ IN-05: Kill Switch Status")


def test_in05_kill_switch_unavailable_disables_actions():
    previous = os.environ.get("BFF_READ_SURFACE_STATE")
    os.environ["BFF_READ_SURFACE_STATE"] = "unavailable"
    try:
        resp = client.get("/api/v1/kill-switch/status", headers={"Authorization": AUTH})
    finally:
        if previous is None:
            os.environ.pop("BFF_READ_SURFACE_STATE", None)
        else:
            os.environ["BFF_READ_SURFACE_STATE"] = previous

    assert resp.status_code == 200
    body = resp.json()
    assert body["kill_switch"]["status"] is None
    assert body["meta"]["surfaces"]["kill_switch"]["status"] == "unavailable"
    assert body["meta"]["surfaces"]["allowedActions"]["status"] == "unavailable"
    assert body["meta"]["degradation"]["kill_switch_reason"]
    assert body["meta"]["degradation"]["allowedActions_reason"]
    assert body["allowedActions"] == {
        "canPause": False,
        "canRiskOff": False,
        "canLiquidateAll": False,
        "canHardRollback": False,
        "canIssueSafeMode": False,
        "secondaryPathAvailable": False,
    }
    print("✅ IN-05: Kill Switch unavailable disables all actions")


def test_in05_kill_switch_admin_only():
    resp = client.get("/api/v1/kill-switch/status", headers={"Authorization": "Bearer op-only:operator"})
    assert resp.status_code == 403
    print("✅ IN-05: Kill Switch requires admin role")


def test_in05_pause_execution_command_schema():
    resp = client.post(
        "/api/v1/operator/commands",
        json=_incident_command_payload(
            "PauseExecution",
            {"pause_new_entries": True, "cancel_open_orders": False},
        ),
        headers=_command_headers(AUTH, "smoke-in05-pause-execution"),
    )
    assert resp.status_code == 202, f"PauseExecution rejected: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["command"] == "PauseExecution"
    print("✅ IN-05: PauseExecution schema accepted")


def test_in05_issue_risk_off_command_schema():
    resp = client.post(
        "/api/v1/operator/commands",
        json=_incident_command_payload(
            "IssueRiskOff",
            {"reduce_exposure_pct": 100},
        ),
        headers=_command_headers(AUTH, "smoke-in05-risk-off"),
    )
    assert resp.status_code == 202, f"IssueRiskOff rejected: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["command"] == "IssueRiskOff"
    print("✅ IN-05: IssueRiskOff schema accepted")


def test_in05_liquidate_all_command_schema():
    resp = client.post(
        "/api/v1/operator/commands",
        json=_incident_command_payload("LiquidateAll", {}),
        headers=_command_headers(ADMIN_MFA_AUTH, "smoke-in05-liquidate-all"),
    )
    assert resp.status_code == 202, f"LiquidateAll rejected: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["command"] == "LiquidateAll"
    print("✅ IN-05: LiquidateAll schema accepted")


def test_in05_hard_rollback_command_schema():
    resp = client.post(
        "/api/v1/operator/commands",
        json=_incident_command_payload(
            "HardRollback",
            {"target_artifact_id": "artifact-fallback-001"},
        ),
        headers=_command_headers(AUTH, "smoke-in05-hard-rollback"),
    )
    assert resp.status_code == 202, f"HardRollback rejected: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["command"] == "HardRollback"
    print("✅ IN-05: HardRollback schema accepted")


def test_in05_issue_safe_mode_command_schema():
    resp = client.post(
        "/api/v1/operator/commands",
        json=_incident_command_payload(
            "IssueSafeMode",
            {"safe_mode_level": "soft"},
        ),
        headers=_command_headers(ADMIN_MFA_AUTH, "smoke-in05-safe-mode"),
    )
    assert resp.status_code == 202, f"IssueSafeMode rejected: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["command"] == "IssueSafeMode"
    print("✅ IN-05: IssueSafeMode schema accepted")


def test_composed_incident_response():
    resp = client.get(
        "/api/v1/operator/incident-response/inc-20260410-001",
        headers={"Authorization": AUTH},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "allowedActions" in body
    assert "meta" in body
    assert "snapshot_at" in body["meta"]
    assert "surfaces" in body["meta"]

    data = body["data"]
    assert "incident" in data
    assert data["incident"]["severity"] in ("sev1", "sev2", "sev3")
    assert "opened_at" in data["incident"]
    assert "affected_bindings" in data
    assert isinstance(data["affected_bindings"], list)
    assert "kill_switch" in data
    assert "runtime_binding" not in data
    assert "telemetry_summary" not in data
    assert "rollbacks" not in data
    assert "evolution_decisions" not in data

    for key in (
        "canPause",
        "canRiskOff",
        "canLiquidateAll",
        "canHardRollback",
        "canIssueSafeMode",
        "canOpenActionDrawer",
    ):
        assert key in body["allowedActions"], f"Missing allowedActions key: {key}"

    surfaces = body["meta"]["surfaces"]
    for surface_name in ["incident", "affected_bindings", "kill_switch", "allowedActions"]:
        assert surface_name in surfaces, f"Missing surface: {surface_name}"
        assert surfaces[surface_name].get("status") in ("ok", "degraded", "unavailable"), (
            f"Unexpected status for {surface_name}"
        )
    print("✅ Composed View: Incident Response")


def test_composed_post_incident_review():
    resp = client.get(
        "/api/v1/operator/post-incident-review/inc-20260409-002",
        headers={"Authorization": AUTH},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body

    data = body["data"]
    assert "incident" in data
    assert "postmortem" in data
    assert "evolution_decisions" in data
    assert "lineage_edges" in data
    assert "telemetry_performance" in data
    print("✅ Composed View: Post-Incident Review")


def test_rbac_denied():
    """Unprivileged role should be denied for incident surfaces."""
    resp = client.get("/api/v1/incidents", headers={"Authorization": "Bearer guest-only:guest"})
    assert resp.status_code == 403
    print("✅ RBAC: Unprivileged role denied access to incident surfaces")


def test_unauthenticated_denied():
    resp = client.get("/api/v1/incidents")
    assert resp.status_code == 401
    print("✅ RBAC: Unauthenticated request denied")


if __name__ == "__main__":
    tests = [
        test_in01_incident_list,
        test_in01_incident_list_filtered,
        test_in01_resolved_incident_list_includes_resolved_at,
        test_in02_incident_detail,
        test_in02_incident_detail_not_found,
        test_in02_missing_backend_does_not_fabricate_incidents,
        test_in03_postmortem_list,
        test_in04_postmortem_detail,
        test_in04_postmortem_not_found,
        test_in05_kill_switch_status,
        test_in05_kill_switch_unavailable_disables_actions,
        test_in05_kill_switch_admin_only,
        test_in05_pause_execution_command_schema,
        test_in05_issue_risk_off_command_schema,
        test_in05_liquidate_all_command_schema,
        test_in05_hard_rollback_command_schema,
        test_in05_issue_safe_mode_command_schema,
        test_composed_incident_response,
        test_composed_post_incident_review,
        test_rbac_denied,
        test_unauthenticated_denied,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"❌ {test_fn.__name__}: {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Smoke test: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("ALL ACCEPTANCE CRITERIA VERIFIED")
