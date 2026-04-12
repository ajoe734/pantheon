#!/usr/bin/env python3
"""Smoke test for APP-002-W2-READ-INCIDENT: Incident Response read surfaces."""
from __future__ import annotations

import sys
import os

# Ensure the BFF module is importable
sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

AUTH = "Bearer test-operator:operator,admin"


def test_in01_incident_list():
    resp = client.get("/api/v1/incidents", headers={"Authorization": AUTH})
    assert resp.status_code == 200, f"IN-01 failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert body["meta"]["total"] >= 1
    print("✅ IN-01: Incident List")


def test_in01_incident_list_filtered():
    resp = client.get("/api/v1/incidents?status=open", headers={"Authorization": AUTH})
    assert resp.status_code == 200
    body = resp.json()
    assert all(i["status"] == "open" for i in body["data"])
    print("✅ IN-01: Incident List (filtered by status=open)")


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
    assert "data" in body
    assert "active_freeze_orders" in body["data"]
    assert "safe_mode_status" in body["data"]
    print("✅ IN-05: Kill Switch Status")


def test_in05_kill_switch_admin_only():
    resp = client.get("/api/v1/kill-switch/status", headers={"Authorization": "Bearer op-only:operator"})
    assert resp.status_code == 403
    print("✅ IN-05: Kill Switch requires admin role")


def test_composed_incident_response():
    resp = client.get(
        "/api/v1/operator/incident-response/inc-20260410-001",
        headers={"Authorization": AUTH},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "meta" in body
    assert "snapshot_at" in body["meta"]
    assert "surfaces" in body["meta"]

    data = body["data"]
    assert "incident" in data
    assert "runtime_binding" in data
    assert "telemetry_summary" in data
    assert "rollbacks" in data
    assert "evolution_decisions" in data
    assert "kill_switch" in data

    surfaces = body["meta"]["surfaces"]
    for surface_name in ["runtime_binding", "telemetry_summary", "rollbacks", "evolution_decisions", "kill_switch"]:
        assert surface_name in surfaces, f"Missing surface: {surface_name}"
        assert surfaces[surface_name].get("status") in ("ok", "degraded"), f"Unexpected status for {surface_name}"
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
    """Viewer role should be denied for incident surfaces."""
    resp = client.get("/api/v1/incidents", headers={"Authorization": "Bearer viewer-only:viewer"})
    assert resp.status_code == 403
    print("✅ RBAC: Viewer denied access to incident surfaces")


def test_unauthenticated_denied():
    resp = client.get("/api/v1/incidents")
    assert resp.status_code == 401
    print("✅ RBAC: Unauthenticated request denied")


if __name__ == "__main__":
    tests = [
        test_in01_incident_list,
        test_in01_incident_list_filtered,
        test_in02_incident_detail,
        test_in02_incident_detail_not_found,
        test_in03_postmortem_list,
        test_in04_postmortem_detail,
        test_in04_postmortem_not_found,
        test_in05_kill_switch_status,
        test_in05_kill_switch_admin_only,
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
