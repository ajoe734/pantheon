"""Regression proof that every Registry mutation route enforces verified-caller
auth (services.runtime_auth_inbound.validate_request_auth), not just the
metadata PATCH route.

Prior state (reviewer finding 1): only PATCH /api/registry/entries/{id}/metadata
called ``_authenticate_registry_write``; register_entry, register_strategy_spec,
register_strategy_artifact, register_allocation_policy_artifact, every
``advance`` route, ``mutate_strategy_artifact_entry``, and
``update_deployment_summary`` were reachable anonymously. This module proves
each of those routes now fails closed, and that a signature-valid JWT missing
subject/tenant/role/expiry claims is still rejected (signature validity alone
is not identity completeness).
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256

from .service import app
from .storage import reset_store

_JWT_SECRET = "registry-auth-wiring-test-secret"


@pytest.fixture(autouse=True)
def clean_store():
    reset_store()
    yield
    reset_store()


@pytest.fixture
def strict_client(monkeypatch):
    """A client against a strict-mode-configured registry service."""
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    return TestClient(app)


def _full_claims(**overrides) -> dict:
    claims = {
        "sub": "operator-1",
        "tenant": "tenant-a",
        "roles": ["operator"],
        "exp": time.time() + 3600,
    }
    claims.update(overrides)
    return claims


def _jwt(**overrides) -> str:
    return encode_jwt_hs256(_full_claims(**overrides), secret=_JWT_SECRET)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_ENTRY_PAYLOAD = {
    "artifact_type": "model_artifact",
    "strategy_id": "auth-wiring-strat",
    "version": "1.0.0",
    "storage_ref": {"backend": "object_store", "path": "s3://bucket/art.bin"},
    "checksum": "sha256:deadbeef",
}


def test_strict_mode_anonymous_register_entry_is_rejected(strict_client):
    resp = strict_client.post("/api/registry/entries", json=_ENTRY_PAYLOAD)
    assert resp.status_code == 401


def test_strict_mode_valid_jwt_register_entry_succeeds(strict_client):
    resp = strict_client.post(
        "/api/registry/entries", json=_ENTRY_PAYLOAD, headers=_bearer(_jwt())
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["entry"]["last_actor"]["actor_id"] == "operator-1"
    assert resp.json()["entry"]["last_actor"]["tenant"] == "tenant-a"


@pytest.mark.parametrize("missing_claim", ["sub", "tenant", "roles", "exp"])
def test_strict_mode_jwt_missing_identity_claim_is_rejected_on_register(
    strict_client, missing_claim
):
    claims = _full_claims()
    claims.pop(missing_claim)
    token = encode_jwt_hs256(claims, secret=_JWT_SECRET)
    resp = strict_client.post(
        "/api/registry/entries", json=_ENTRY_PAYLOAD, headers=_bearer(token)
    )
    assert resp.status_code in (401, 403)


@pytest.mark.parametrize("missing_claim", ["sub", "tenant", "roles", "exp"])
def test_strict_mode_jwt_missing_identity_claim_is_rejected_on_metadata_patch(
    strict_client, missing_claim
):
    created = strict_client.post(
        "/api/registry/entries", json=_ENTRY_PAYLOAD, headers=_bearer(_jwt())
    ).json()
    registry_id = created["entry"]["registry_id"]

    claims = _full_claims()
    claims.pop(missing_claim)
    token = encode_jwt_hs256(claims, secret=_JWT_SECRET)
    resp = strict_client.patch(
        f"/api/registry/entries/{registry_id}/metadata",
        json={"expected_metadata": None, "metadata": {"note": "x"}},
        headers=_bearer(token),
    )
    assert resp.status_code in (401, 403)


def test_strict_mode_anonymous_advance_is_rejected(strict_client):
    created = strict_client.post(
        "/api/registry/entries",
        json={**_ENTRY_PAYLOAD, "lineage": {"source_run_ids": ["run-1"]}},
        headers=_bearer(_jwt()),
    ).json()
    registry_id = created["entry"]["registry_id"]

    resp = strict_client.post(
        f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "candidate", "expected_artifact_state": "draft"},
    )
    assert resp.status_code == 401


def test_strict_mode_valid_jwt_advance_succeeds_and_binds_actor(strict_client):
    created = strict_client.post(
        "/api/registry/entries",
        json={**_ENTRY_PAYLOAD, "lineage": {"source_run_ids": ["run-1"]}},
        headers=_bearer(_jwt()),
    ).json()
    registry_id = created["entry"]["registry_id"]

    resp = strict_client.post(
        f"/api/registry/entries/{registry_id}/advance",
        json={"target_state": "candidate", "expected_artifact_state": "draft"},
        headers=_bearer(_jwt(sub="approver-2")),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["entry"]["last_actor"]["actor_id"] == "approver-2"


def test_strict_mode_anonymous_update_deployment_summary_is_rejected(strict_client):
    resp = strict_client.put(
        "/api/registry/entries/reg-does-not-exist/deployment-summary",
        json={"current_stage": "paper"},
    )
    assert resp.status_code == 401


def test_strict_mode_anonymous_register_strategy_artifact_is_rejected(strict_client):
    resp = strict_client.post(
        "/api/registry/strategy-artifacts",
        json={
            "strategy_artifact": {"artifact_id": "sa-1"},
        },
    )
    assert resp.status_code == 401


def test_strict_mode_anonymous_register_allocation_policy_artifact_is_rejected(strict_client):
    resp = strict_client.post(
        "/api/registry/allocation-policy-artifacts",
        json={
            "version": "1.0.0",
            "allocation_policy_artifact": {"capital_pool_id": "pool-1"},
        },
    )
    assert resp.status_code == 401


def test_permissive_mode_still_accepts_structured_token_without_full_jwt_claims():
    """Permissive-mode structured legacy tokens (the test-double form used
    across this service's other unit tests) remain accepted — the stricter
    claim-completeness check applies only to real JWTs, which have claims to
    be missing in the first place."""
    reset_store()
    client = TestClient(app, headers={"Authorization": "Bearer test-operator:operator"})
    resp = client.post("/api/registry/entries", json=_ENTRY_PAYLOAD)
    assert resp.status_code == 200, resp.text


def test_deployment_view_fails_closed_503_when_owner_unreachable_or_unauthenticated(
    strict_client, monkeypatch
):
    """REGISTRY-DEPLOYMENT-VIEW-OWNER-READ-20261018:
    - docker-compose.yml wires PANTHEON_RUNTIME_MANAGER_URL and PANTHEON_DEPLOYMENT_API_URL into registry.
    - GET /api/registry/strategies/{strategy_id}/deployment-view fails closed with 503 over real HTTP when:
      * PANTHEON_RUNTIME_MANAGER_URL and/or PANTHEON_DEPLOYMENT_API_URL are unset;
      * configured runtime-manager or deployment owner endpoints are unreachable;
      * owner endpoints return 401, 403, 500, or malformed payloads ('{}' missing 'bindings' or non-JSON);
      * owner endpoints return 301/302/307/308 redirects (refused via NoOwnerRedirect without forwarding Authorization/X-Tenant-Id).
    - Immutable GET /api/registry/entries/{id} makes zero outbound owner callbacks.
    """
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import os
    from pathlib import Path
    import threading
    from services.governance.test_approval_authority import (
        advance_registry_http,
        configure_registry_unit_auth,
    )
    from .test_service import (
        _coerce_deployment_plan,
        _coerce_runtime_binding,
        _get_real_owner_apps,
    )

    compose_text = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    assert "PANTHEON_RUNTIME_MANAGER_URL: ${PANTHEON_RUNTIME_MANAGER_URL:-http://runtime-manager:8081}" in compose_text
    assert "PANTHEON_DEPLOYMENT_API_URL: ${PANTHEON_DEPLOYMENT_API_URL:-http://deployment:8095}" in compose_text

    configure_registry_unit_auth(monkeypatch)
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    token = _jwt(roles=["operator"], tenant="tenant-a")
    headers = {"Authorization": f"Bearer {token}"}

    # Anonymous/missing caller auth on deployment-view fails closed with 401
    anon_dv = strict_client.get("/api/registry/strategies/auth-wiring-strat/deployment-view")
    assert anon_dv.status_code == 401

    created = strict_client.post(
        "/api/registry/entries",
        json={**_ENTRY_PAYLOAD, "lineage": {"source_run_ids": ["run-auth-1"]}},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["entry"]["registry_id"]
    for target, expected in (("candidate", "draft"), ("approved", "candidate")):
        r = advance_registry_http(
            strict_client,
            f"/api/registry/entries/{rid}/advance",
            json={"target_state": target, "expected_artifact_state": expected},
            headers=headers,
        )
        assert r.status_code == 200, r.text

    # Populate cached deployment_summary to prove 503 never falls back to cached stage
    put_resp = strict_client.put(
        f"/api/registry/entries/{rid}/deployment-summary",
        json={"current_stage": "paper", "deployment_plan_id": "cached-plan", "runtime_binding_id": "cached-rb"},
        headers=headers,
    )
    assert put_resp.status_code == 200, put_resp.text

    # 1. Missing both owner URLs -> 503 (never returns current_stage='none')
    monkeypatch.delenv("PANTHEON_RUNTIME_MANAGER_URL", raising=False)
    monkeypatch.delenv("PANTHEON_DEPLOYMENT_API_URL", raising=False)
    resp_missing_both = strict_client.get(
        "/api/registry/strategies/auth-wiring-strat/deployment-view",
        headers=headers,
    )
    assert resp_missing_both.status_code == 503, resp_missing_both.text

    # 2. Unreachable runtime-manager -> 503
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", "http://127.0.0.1:9")
    resp_unreachable = strict_client.get(
        "/api/registry/strategies/auth-wiring-strat/deployment-view",
        headers=headers,
    )
    assert resp_unreachable.status_code == 503, resp_unreachable.text

    mods = _get_real_owner_apps()
    rm_main = mods["rm_main"]
    dep_svc = mods["dep_svc"]
    RuntimeBinding = mods["RuntimeBinding"]
    RuntimeBindingStore = mods["RuntimeBindingStore"]
    DeploymentPlan = mods["DeploymentPlan"]
    DeploymentPlanStore = mods["DeploymentPlanStore"]

    rm_store = RuntimeBindingStore(path=None)
    rm_store.create(
        _coerce_runtime_binding(
            RuntimeBinding,
            {
                "binding_id": "rb-auth-1",
                "capital_pool_id": "pool-auth-1",
                "artifact_id": rid,
                "artifact_version": "1.0.0",
                "deployment_mode": "paper",
                "plan_id": "plan-auth-1",
                "status": "active",
                "effective_at": "2026-10-18T00:00:00Z",
                "metadata": {"tenant_id": "tenant-a", "strategy_id": "auth-wiring-strat"},
            },
        ),
        single_runtime_enforced=True,
    )
    plan_store = DeploymentPlanStore(storage_path=None)
    plan_store.put(
        _coerce_deployment_plan(
            DeploymentPlan,
            "plan-auth-1",
            {
                "plan_id": "plan-auth-1",
                "strategy_id": "auth-wiring-strat",
                "capital_pool_id": "pool-auth-1",
                "artifact_id": rid,
                "artifact_version": "1.0.0",
                "target_stage": "paper",
                "status": "approved",
                "tenant_id": "tenant-a",
                "binding_id": "rb-auth-1",
            },
        )
    )

    class _InMemoryRuntimeService:
        def __init__(self, store):
            self._store = store

        def list_all(self):
            return self._store.list_all()

        def list_by_pool(self, pool_id: str):
            return self._store.find_by_pool(pool_id)

        def list_by_plan(self, plan_id: str):
            return self._store.find_by_plan(plan_id)

        def get(self, binding_id: str):
            return self._store.get(binding_id)

    prev_rm_svc = rm_main._svc
    prev_dep_store = dep_svc.store
    prev_planner_store = dep_svc.planner_service.plan_store
    rm_main._svc = _InMemoryRuntimeService(rm_store)
    dep_svc.store = plan_store
    dep_svc.planner_service.plan_store = plan_store

    rm_client = rm_main.app.test_client()
    dep_client = TestClient(dep_svc.app)

    state = {"mode": "real", "requests": [], "redirect_hits": 0, "last_owner_status": None}

    class _OwnerHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            req_auth = self.headers.get("Authorization")
            req_tenant = self.headers.get("X-Tenant-Id")
            state["requests"].append(
                {
                    "path": self.path,
                    "authorization": req_auth,
                    "tenant": req_tenant,
                }
            )
            if self.path == "/redirect-target":
                state["redirect_hits"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"bindings": []}')
                return
            mode = state["mode"]
            if mode == "rm_500" and self.path == "/api/runtime-bindings":
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"internal error")
                return
            if mode == "rm_malformed_empty_dict" and self.path == "/api/runtime-bindings":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            if mode == "rm_redirect" and self.path == "/api/runtime-bindings":
                self.send_response(302)
                self.send_header("Location", f"{base_url}/redirect-target")
                self.end_headers()
                return
            if mode == "dep_redirect" and self.path.startswith("/api/deployment/"):
                self.send_response(307)
                self.send_header("Location", f"{base_url}/redirect-target")
                self.end_headers()
                return

            fwd_headers = {}
            if req_auth is not None:
                fwd_headers["Authorization"] = req_auth
            if req_tenant is not None:
                if (
                    (mode == "rm_foreign_tenant_hdr" and self.path.startswith("/api/runtime-bindings"))
                    or (mode == "dep_foreign_tenant_hdr" and self.path.startswith("/api/deployment/"))
                ):
                    fwd_headers["X-Tenant-Id"] = "tenant-foreign"
                else:
                    fwd_headers["X-Tenant-Id"] = req_tenant

            if self.path.startswith("/api/runtime-bindings"):
                resp = rm_client.get(self.path, headers=fwd_headers)
                state["last_owner_status"] = resp.status_code
                self.send_response(resp.status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp.get_data())
                return
            if self.path.startswith("/api/deployment/"):
                resp = dep_client.get(self.path, headers=fwd_headers)
                state["last_owner_status"] = resp.status_code
                self.send_response(resp.status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp.content)
                return

    @contextmanager
    def _run_server():
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _OwnerHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            yield f"http://127.0.0.1:{srv.server_port}"
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(timeout=2.0)
            rm_main._svc = prev_rm_svc
            dep_svc.store = prev_dep_store
            dep_svc.planner_service.plan_store = prev_planner_store

    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_AUDIENCE", "")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_JWT_AUDIENCE", "")

    with _run_server() as base_url:
        monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", base_url)
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", base_url)

        # 3. Immutable EntryGET makes zero outbound owner callbacks
        state["requests"].clear()
        entry_get = strict_client.get(f"/api/registry/entries/{rid}", headers=headers)
        assert entry_get.status_code == 200, entry_get.text
        assert state["requests"] == []

        # Baseline real-app strict JWT + tenant success
        ok_resp = strict_client.get(
            "/api/registry/strategies/auth-wiring-strat/deployment-view",
            headers=headers,
        )
        assert ok_resp.status_code == 200, ok_resp.text
        assert ok_resp.json()["current_stage"] == "paper"

        # 4a. Real runtime-manager 401 (bad JWT signature) -> Registry 503
        monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "wrong-rm-secret")
        resp_rm_401 = strict_client.get(
            "/api/registry/strategies/auth-wiring-strat/deployment-view",
            headers=headers,
        )
        assert state["last_owner_status"] == 401
        assert resp_rm_401.status_code == 503, resp_rm_401.text
        monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", _JWT_SECRET)

        # 4b. Real deployment 401 (bad JWT signature) -> Registry 503
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_JWT_SECRET", "wrong-dep-secret")
        resp_dep_401 = strict_client.get(
            "/api/registry/strategies/auth-wiring-strat/deployment-view",
            headers=headers,
        )
        assert state["last_owner_status"] == 401
        assert resp_dep_401.status_code == 503, resp_dep_401.text
        monkeypatch.setenv("PANTHEON_DEPLOYMENT_JWT_SECRET", _JWT_SECRET)

        # 4c. Real runtime-manager 403 (role registry-reader not in _BINDING_READ_ROLES) -> Registry 503
        reader_only_hdr = _bearer(_jwt(roles=["registry-reader"], tenant="tenant-a"))
        resp_rm_403_role = strict_client.get(
            "/api/registry/strategies/auth-wiring-strat/deployment-view",
            headers=reader_only_hdr,
        )
        assert state["last_owner_status"] == 403
        assert resp_rm_403_role.status_code == 503, resp_rm_403_role.text

        # 4d. Real deployment 403 (roles registry-reader+viewer pass Registry and runtime-manager, rejected by deployment) -> Registry 503
        reader_viewer_hdr = _bearer(_jwt(roles=["registry-reader", "viewer"], tenant="tenant-a"))
        resp_dep_403_role = strict_client.get(
            "/api/registry/strategies/auth-wiring-strat/deployment-view",
            headers=reader_viewer_hdr,
        )
        assert state["last_owner_status"] == 403
        assert resp_dep_403_role.status_code == 503, resp_dep_403_role.text

        # 4e. Real runtime-manager 403 (TENANT_SCOPE_FORBIDDEN) & real deployment 403 (TENANT_BOUNDARY_DENIED) -> Registry 503
        for tenant_mode in ("rm_foreign_tenant_hdr", "dep_foreign_tenant_hdr"):
            state["mode"] = tenant_mode
            resp_403_tenant = strict_client.get(
                "/api/registry/strategies/auth-wiring-strat/deployment-view",
                headers=headers,
            )
            assert state["last_owner_status"] == 403
            assert resp_403_tenant.status_code == 503, resp_403_tenant.text

        # 5. Fault modes: 500, malformed empty dict '{}', and 302/307 redirects -> 503 with zero redirect hits
        for mode in ("rm_500", "rm_malformed_empty_dict", "rm_redirect", "dep_redirect"):
            state["mode"] = mode
            resp = strict_client.get(
                "/api/registry/strategies/auth-wiring-strat/deployment-view",
                headers=headers,
            )
            assert resp.status_code == 503, f"mode={mode} expected 503, got {resp.status_code}: {resp.text}"
        assert state["redirect_hits"] == 0



