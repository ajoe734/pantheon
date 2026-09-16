"""Contract and verification tests for ManagementSessionHarness.

Validates the standalone real-router management session harness required before B03
can decouple its own six migration tests:
  1. Standalone import assertion (zero main.py reverse imports in isolated subprocess).
  2. Mounting and 200 OK responses across all six real endpoints:
     - /bff/me
     - /bff/management/persona-fleet
     - /bff/strategies
     - /bff/personas
     - /bff/management/human-inbox
     - /bff/management/evidence
  3. Two isolated SessionLifecycleStore instances: logging out one yields 401
     SESSION_LOGGED_OUT across all 6 routes while the second remains 200 OK.
  4. Role-less rejection (403 role_check) across all 6 routes.
  5. Cross-tenant rejection (403 tenant_scope) across all 6 routes.
  6. Cookie session authentication and logout across all 6 routes.
  7. JWT bearer authentication and logout across all 6 routes.
  8. Logout idempotency replay with matching idempotency-key.
  9. Typed in-memory test doubles isolation and pre-seeding.
  10. Context manager lifecycle and temporary resource cleanup.
  11. Async client transport support via httpx.AsyncClient.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest

from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
from services.control_plane.bff.tests.management_session_harness import (
    InMemoryManagementReadStore,
    InMemoryRankingWriteOwner,
    InMemoryStrategyReadStore,
    ManagementSessionHarness,
)


def _extract_error(resp: httpx.Response | Any) -> Dict[str, Any]:
    """Helper to extract error payload from Pack-D JSON or FastAPI detail."""
    try:
        body = resp.json()
    except Exception:
        return {}
    if isinstance(body, dict):
        if "error" in body and isinstance(body["error"], dict):
            return body["error"]
        if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
            return body["detail"]["error"]
    return {}


# ==============================================================================
# 1. Standalone Import Assertion (Subprocess - Zero main.py)
# ==============================================================================

def test_standalone_import_no_main():
    """Verify that management_session_harness can be imported in an isolated process
    without importing services.control_plane.bff.main or root main.
    """
    code = (
        "import sys\n"
        "from services.control_plane.bff.tests.management_session_harness import (\n"
        "    ManagementSessionHarness,\n"
        "    InMemoryManagementReadStore,\n"
        "    InMemoryStrategyReadStore,\n"
        "    InMemoryRankingWriteOwner,\n"
        ")\n"
        "modules = set(sys.modules.keys())\n"
        "assert 'services.control_plane.bff.main' not in modules, (\n"
        "    f'services.control_plane.bff.main unexpectedly imported: {modules}'\n"
        ")\n"
        "assert 'main' not in modules, (\n"
        "    f'main unexpectedly imported: {modules}'\n"
        ")\n"
        "print('STANDALONE_IMPORT_CLEAN')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "STANDALONE_IMPORT_CLEAN" in result.stdout


# ==============================================================================
# 2. Mounts All Six Real Endpoints (200 OK)
# ==============================================================================

def test_harness_mounts_all_six_endpoints(tmp_path: Path):
    """Mount real routers and verify all 6 core endpoints return 200 OK with valid payloads."""
    store = SessionLifecycleStore(str(tmp_path / "store.json"))
    with ManagementSessionHarness(store=store) as harness:
        client = harness.create_client()
        auth_headers = {"Authorization": harness.make_bearer_token()}

        # 1. /bff/me
        resp_me = client.get("/bff/me", headers=auth_headers)
        assert resp_me.status_code == 200, f"/bff/me failed: {resp_me.text}"
        data_me = resp_me.json()
        assert "user" in data_me or "data" in data_me

        # 2. /bff/management/persona-fleet
        resp_fleet = client.get("/bff/management/persona-fleet", headers=auth_headers)
        assert resp_fleet.status_code == 200, f"/bff/management/persona-fleet failed: {resp_fleet.text}"
        data_fleet = resp_fleet.json()
        assert "items" in data_fleet or "data" in data_fleet

        # 3. /bff/strategies
        resp_strat = client.get("/bff/strategies", headers=auth_headers)
        assert resp_strat.status_code == 200, f"/bff/strategies failed: {resp_strat.text}"
        data_strat = resp_strat.json()
        assert "items" in data_strat or "data" in data_strat

        # 4. /bff/personas
        resp_personas = client.get("/bff/personas", headers=auth_headers)
        assert resp_personas.status_code == 200, f"/bff/personas failed: {resp_personas.text}"
        data_personas = resp_personas.json()
        assert "items" in data_personas or "data" in data_personas

        # 5. /bff/management/human-inbox
        resp_inbox = client.get("/bff/management/human-inbox", headers=auth_headers)
        assert resp_inbox.status_code == 200, f"/bff/management/human-inbox failed: {resp_inbox.text}"
        data_inbox = resp_inbox.json()
        assert "items" in data_inbox or "data" in data_inbox or "summary" in data_inbox

        # 6. /bff/management/evidence
        resp_evidence = client.get("/bff/management/evidence", headers=auth_headers)
        assert resp_evidence.status_code == 200, f"/bff/management/evidence failed: {resp_evidence.text}"
        data_evidence = resp_evidence.json()
        assert "items" in data_evidence or "data" in data_evidence or "evidence_refs" in data_evidence


# ==============================================================================
# 3. Two Isolated Stores & Independent Logout Isolation
# ==============================================================================

def test_independent_session_lifecycle_stores_and_logout_isolation(tmp_path: Path):
    """Mount two distinct harness instances with separate stores.
    1. Both respond 200 to all 6 endpoints.
    2. Logging out of instance 1 yields 401 SESSION_LOGGED_OUT across all 6 routes on instance 1.
    3. Instance 2 remains 200 OK across all 6 routes.
    4. Logging out of instance 2 yields 401 SESSION_LOGGED_OUT across all 6 routes on instance 2.
    """
    store1 = SessionLifecycleStore(str(tmp_path / "store1.json"))
    store2 = SessionLifecycleStore(str(tmp_path / "store2.json"))

    with ManagementSessionHarness(store=store1) as h1, ManagementSessionHarness(store=store2) as h2:
        c1 = h1.create_client()
        c2 = h2.create_client()

        auth_headers = {"Authorization": h1.make_bearer_token(operator_id="shared-op")}

        # Step 1: Both start authenticated (200) across all 6 routes
        for route in ManagementSessionHarness.CORE_ROUTES:
            r1 = c1.get(route, headers=auth_headers)
            r2 = c2.get(route, headers=auth_headers)
            assert r1.status_code == 200, f"h1 {route} expected 200 before logout, got {r1.status_code}"
            assert r2.status_code == 200, f"h2 {route} expected 200 before logout, got {r2.status_code}"

        # Step 2: Logout instance 1
        logout_resp1 = c1.post("/bff/logout", headers=auth_headers, json={})
        assert logout_resp1.status_code == 200, f"h1 logout failed: {logout_resp1.text}"

        # Step 3: Instance 1 yields 401 SESSION_LOGGED_OUT across all 6 routes
        for route in ManagementSessionHarness.CORE_ROUTES:
            r1_after = c1.get(route, headers=auth_headers)
            assert r1_after.status_code == 401, (
                f"h1 {route} expected 401 after logout, got {r1_after.status_code}"
            )
            err = _extract_error(r1_after)
            reason = err.get("details", {}).get("reason")
            assert reason == "SESSION_LOGGED_OUT", (
                f"h1 {route} expected reason SESSION_LOGGED_OUT, got {err}"
            )

        # Step 4: Instance 2 remains 200 OK across all 6 routes
        for route in ManagementSessionHarness.CORE_ROUTES:
            r2_still_active = c2.get(route, headers=auth_headers)
            assert r2_still_active.status_code == 200, (
                f"h2 {route} expected 200 while h1 logged out, got {r2_still_active.status_code}"
            )

        # Step 5: Logout instance 2
        logout_resp2 = c2.post("/bff/logout", headers=auth_headers, json={})
        assert logout_resp2.status_code == 200

        # Step 6: Instance 2 now yields 401 SESSION_LOGGED_OUT across all 6 routes
        for route in ManagementSessionHarness.CORE_ROUTES:
            r2_after = c2.get(route, headers=auth_headers)
            assert r2_after.status_code == 401, (
                f"h2 {route} expected 401 after logout, got {r2_after.status_code}"
            )
            err = _extract_error(r2_after)
            assert err.get("details", {}).get("reason") == "SESSION_LOGGED_OUT"


# ==============================================================================
# 4. Role-less Rejection Across All Endpoints (403 role_check)
# ==============================================================================

def test_roleless_rejection_across_all_endpoints(tmp_path: Path):
    """Callers with an empty/unrecognized role must receive 403 role_check across all 6 routes."""
    store = SessionLifecycleStore(str(tmp_path / "store.json"))
    with ManagementSessionHarness(store=store) as harness:
        client = harness.create_client()
        roleless_headers = {
            "Authorization": harness.make_bearer_token(
                operator_id="roleless-caller", roles=("unauthorized_guest",)
            )
        }

        for route in ManagementSessionHarness.CORE_ROUTES:
            resp = client.get(route, headers=roleless_headers)
            assert resp.status_code == 403, (
                f"{route} expected 403 for roleless caller, got {resp.status_code}"
            )
            err = _extract_error(resp)
            precondition = err.get("details", {}).get("precondition_failed")
            assert precondition == "role_check", (
                f"{route} expected precondition_failed 'role_check', got {err}"
            )


# ==============================================================================
# 5. Cross-Tenant Rejection Across All Endpoints (403 tenant_scope)
# ==============================================================================

def test_cross_tenant_rejection_across_all_endpoints(tmp_path: Path):
    """Requests targeting a tenant outside the identity's scope must receive 403 tenant_scope."""
    store = SessionLifecycleStore(str(tmp_path / "store.json"))
    with ManagementSessionHarness(store=store, allowed_tenants=["tenant-alpha"]) as harness:
        client = harness.create_client()
        auth_headers = {
            "Authorization": harness.make_bearer_token(
                operator_id="scoped-op", tenant_ids=("tenant-alpha",)
            ),
            "X-Tenant-Id": "tenant-forbidden",
        }

        for route in ManagementSessionHarness.CORE_ROUTES:
            resp = client.get(route, headers=auth_headers)
            assert resp.status_code == 403, (
                f"{route} expected 403 for cross-tenant request, got {resp.status_code}"
            )
            err = _extract_error(resp)
            precondition = err.get("details", {}).get("precondition_failed")
            assert precondition == "tenant_scope", (
                f"{route} expected precondition_failed 'tenant_scope', got {err}"
            )


# ==============================================================================
# 6. Cookie Session Flow & Logout Across All Endpoints
# ==============================================================================

def test_cookie_session_flow_and_logout_across_all_endpoints(tmp_path: Path):
    """Authentication via pantheon_session cookie:
    1. Returns 200 OK across all 6 routes.
    2. POST /bff/logout with cookie clears cookie and marks session logged out.
    3. Subsequent requests with that cookie return 401 SESSION_LOGGED_OUT across all 6 routes.
    """
    store = SessionLifecycleStore(str(tmp_path / "store.json"))
    with ManagementSessionHarness(store=store) as harness:
        client = harness.create_client()
        jwt_token = harness.make_jwt_token(operator_id="cookie-op", roles=("operator", "admin"))
        client.cookies.set("pantheon_session", jwt_token)

        # 1. 200 OK on all 6 routes with cookie
        for route in ManagementSessionHarness.CORE_ROUTES:
            resp = client.get(route)
            assert resp.status_code == 200, f"{route} expected 200 with cookie, got {resp.status_code}"

        # 2. Logout via cookie
        logout_resp = client.post("/bff/logout", json={})
        assert logout_resp.status_code == 200, f"Logout failed: {logout_resp.text}"

        # 3. Setting the cookie again must return 401 SESSION_LOGGED_OUT across all 6 routes
        client.cookies.set("pantheon_session", jwt_token)
        for route in ManagementSessionHarness.CORE_ROUTES:
            resp_after = client.get(route)
            assert resp_after.status_code == 401, (
                f"{route} expected 401 after cookie logout, got {resp_after.status_code}"
            )
            err = _extract_error(resp_after)
            assert err.get("details", {}).get("reason") == "SESSION_LOGGED_OUT"


# ==============================================================================
# 7. JWT Signed Token Flow & Logout
# ==============================================================================

def test_jwt_signed_token_flow_and_logout(tmp_path: Path):
    """Authentication via HS256 JWT Authorization Bearer token."""
    store = SessionLifecycleStore(str(tmp_path / "store.json"))
    with ManagementSessionHarness(store=store) as harness:
        client = harness.create_client()
        jwt_token = harness.make_jwt_token(operator_id="jwt-user", roles=("operator", "viewer"))
        jwt_headers = {"Authorization": f"Bearer {jwt_token}"}

        # 1. 200 OK on all 6 routes
        for route in ManagementSessionHarness.CORE_ROUTES:
            resp = client.get(route, headers=jwt_headers)
            assert resp.status_code == 200, f"{route} expected 200 with JWT, got {resp.status_code}"

        # 2. Logout
        logout_resp = client.post("/bff/logout", headers=jwt_headers, json={})
        assert logout_resp.status_code == 200

        # 3. 401 SESSION_LOGGED_OUT
        for route in ManagementSessionHarness.CORE_ROUTES:
            resp_after = client.get(route, headers=jwt_headers)
            assert resp_after.status_code == 401, f"{route} expected 401, got {resp_after.status_code}"
            err = _extract_error(resp_after)
            assert err.get("details", {}).get("reason") == "SESSION_LOGGED_OUT"


# ==============================================================================
# 8. Logout Idempotency
# ==============================================================================

def test_logout_idempotency(tmp_path: Path):
    """Calling /bff/logout with an Idempotency-Key returns cached result on replay."""
    store = SessionLifecycleStore(str(tmp_path / "store.json"))
    with ManagementSessionHarness(store=store) as harness:
        client = harness.create_client()
        headers = {
            "Authorization": harness.make_bearer_token(operator_id="idem-op"),
            "Idempotency-Key": "logout-idem-key-001",
        }

        # First logout
        resp1 = client.post("/bff/logout", headers=headers, json={"reason": "user_action"})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1.get("meta", {}).get("idempotency", {}).get("replayed") is False

        # Replay logout with exact same key and payload
        resp2 = client.post("/bff/logout", headers=headers, json={"reason": "user_action"})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2.get("meta", {}).get("idempotency", {}).get("replayed") is True


# ==============================================================================
# 9. Typed Test Doubles & Data Isolation
# ==============================================================================

def test_typed_test_doubles_and_store_isolation(tmp_path: Path):
    """Verify in-memory test doubles support pre-seeded records and remain isolated."""
    pre_approvals = [{"id": "appr-1", "decision_id": "appr-1", "decision_state": "pending"}]
    pre_evidence = [{"evidence_id": "ev-1", "ref_id": "ref-1", "credibility_tier": "tier1"}]
    mgmt_store = InMemoryManagementReadStore(approvals=pre_approvals, evidence=pre_evidence)

    pre_summaries = [{"strategy_id": "strat-1", "state": "active", "name": "Alpha Strat"}]
    strat_store = InMemoryStrategyReadStore(summaries=pre_summaries)

    with ManagementSessionHarness(
        store_path=tmp_path / "seeded_store.json",
        mgmt_read_store=mgmt_store,
        strat_read_store=strat_store,
    ) as harness:
        client = harness.create_client()
        headers = {"Authorization": harness.make_bearer_token()}

        # Verify pre-seeded inbox row is returned
        resp_inbox = client.get("/bff/management/human-inbox", headers=headers)
        assert resp_inbox.status_code == 200
        inbox_json = resp_inbox.json()
        if isinstance(inbox_json.get("data"), dict):
            items_inbox = inbox_json["data"].get("items", [])
        else:
            items_inbox = inbox_json.get("items") or inbox_json.get("data", [])
        assert any("appr-1" in str(item) for item in items_inbox)

        resp_evidence = client.get("/bff/management/evidence", headers=headers)
        assert resp_evidence.status_code == 200
        data_ev = resp_evidence.json()
        assert "ev-1" in str(data_ev) or "ref-1" in str(data_ev)

        # Verify pre-seeded strategy is returned
        resp_strat = client.get("/bff/strategies", headers=headers)
        assert resp_strat.status_code == 200
        items_strat = resp_strat.json().get("items") or resp_strat.json().get("data", [])
        assert any(s.get("strategy_id") == "strat-1" or s.get("id") == "strat-1" for s in items_strat)


# ==============================================================================
# 10. Context Manager Cleanup
# ==============================================================================

def test_context_manager_cleanup():
    """Verify that omitting store and store_path creates a temporary directory
    that is cleanly deleted upon context exit.
    """
    temp_dir_path: Optional[str] = None
    with ManagementSessionHarness() as harness:
        temp_dir_path = harness._temp_dir
        assert temp_dir_path is not None
        assert os.path.isdir(temp_dir_path)
        client = harness.create_client()
        resp = client.get("/bff/me", headers={"Authorization": harness.make_bearer_token()})
        assert resp.status_code == 200

    assert temp_dir_path is not None
    assert not os.path.exists(temp_dir_path)


# ==============================================================================
# 11. Async Client Support
# ==============================================================================

@pytest.mark.anyio
async def test_async_client_support(tmp_path: Path):
    """Verify create_async_client works with httpx.AsyncClient across the 6 endpoints."""
    store = SessionLifecycleStore(str(tmp_path / "async_store.json"))
    with ManagementSessionHarness(store=store) as harness:
        auth_headers = {"Authorization": harness.make_bearer_token()}
        async with harness.create_async_client() as client:
            for route in ManagementSessionHarness.CORE_ROUTES:
                resp = await client.get(route, headers=auth_headers)
                assert resp.status_code == 200, f"Async {route} failed: {resp.status_code}"
