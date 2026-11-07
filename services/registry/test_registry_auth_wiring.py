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
    from .storage import get_store
    from .test_service import (
        _coerce_deployment_plan,
        _coerce_runtime_binding,
        _get_real_owner_apps,
        _snapshot_owner_and_registry_state,
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
            json={
                "target_state": target,
                "expected_artifact_state": expected,
                "command_key": f"cmd-auth-advance-{rid}-{target}",
            },
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
    assert mods["startup_isolation"]["verified"] is True
    rm_main = mods["rm_main"]
    dep_svc = mods["dep_svc"]
    RuntimeBinding = mods["RuntimeBinding"]
    RuntimeBindingStore = mods["RuntimeBindingStore"]
    DeploymentPlan = mods["DeploymentPlan"]
    DeploymentPlanStore = mods["DeploymentPlanStore"]
    DeploymentSagaStore = mods["DeploymentSagaStore"]

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
    seeded_plan = _coerce_deployment_plan(
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
    plan_store.put(seeded_plan)
    saga_store = DeploymentSagaStore(storage_path=None)
    boot = saga_store.bootstrap_for_plan(
        seeded_plan,
        trace_id="trace-plan-auth-1",
        saga_id="saga-plan-auth-1",
        metadata=dict(seeded_plan.metadata or {}),
    )
    saga_store.consume_event("runtime-manager", boot.outbox_event.event)

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
    prev_saga_store = dep_svc.saga_store
    prev_orch_saga_store = dep_svc.orchestration_service.saga_store
    prev_proj_saga_store = dep_svc.projection_service.saga_store
    rm_main._svc = _InMemoryRuntimeService(rm_store)
    dep_svc.store = plan_store
    dep_svc.planner_service.plan_store = plan_store
    dep_svc.saga_store = saga_store
    dep_svc.orchestration_service.saga_store = saga_store
    dep_svc.projection_service.saga_store = saga_store

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
                    "method": "GET",
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
            dep_svc.saga_store = prev_saga_store
            dep_svc.orchestration_service.saga_store = prev_orch_saga_store
            dep_svc.projection_service.saga_store = prev_proj_saga_store

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

        baseline_snapshot = _snapshot_owner_and_registry_state(
            get_store(), rm_store, plan_store, saga_store
        )
        assert rid in baseline_snapshot["registry_entries"]
        assert len(baseline_snapshot["registry_command_receipts"]) >= 2
        assert len(baseline_snapshot["runtime_bindings"]) == 1
        assert len(baseline_snapshot["deployment_plans"]) == 1
        assert len(baseline_snapshot["deployment_sagas"]) == 1
        assert len(baseline_snapshot["deployment_outbox_records"]) == 1
        assert len(baseline_snapshot["deployment_inbox_receipts"]) == 1

        # 3. Immutable EntryGET makes zero outbound owner callbacks
        state["requests"].clear()
        entry_get = strict_client.get(f"/api/registry/entries/{rid}", headers=headers)
        assert entry_get.status_code == 200, entry_get.text
        assert state["requests"] == []
        assert _snapshot_owner_and_registry_state(
            get_store(), rm_store, plan_store, saga_store
        ) == baseline_snapshot

        # Baseline real-app strict JWT + tenant success
        ok_resp = strict_client.get(
            "/api/registry/strategies/auth-wiring-strat/deployment-view",
            headers=headers,
        )
        assert ok_resp.status_code == 200, ok_resp.text
        assert ok_resp.json()["current_stage"] == "paper"
        assert _snapshot_owner_and_registry_state(
            get_store(), rm_store, plan_store, saga_store
        ) == baseline_snapshot

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
        assert _snapshot_owner_and_registry_state(
            get_store(), rm_store, plan_store, saga_store
        ) == baseline_snapshot


def test_strategy_artifacts_catalog_requires_verified_read_auth(strict_client):
    """GET /api/registry/strategy-artifacts enforces strict read authentication and X-Tenant-Id match."""
    unauth = strict_client.get("/api/registry/strategy-artifacts")
    assert unauth.status_code in (401, 403)

    token_a = _jwt(roles=["registry-reader"], tenant="tenant-a")
    mismatch = strict_client.get(
        "/api/registry/strategy-artifacts",
        headers={**_bearer(token_a), "X-Tenant-Id": "tenant-b"},
    )
    assert mismatch.status_code == 403

    ok = strict_client.get(
        "/api/registry/strategy-artifacts",
        headers={**_bearer(token_a), "X-Tenant-Id": "tenant-a"},
    )
    assert ok.status_code == 200
    assert "items" in ok.json()
    assert "total" in ok.json()


@pytest.mark.parametrize("read_role", ["viewer", "view_only"])
def test_strict_mode_viewer_and_view_only_can_read_same_tenant_get_surfaces_and_deny_cross_tenant(
    strict_client, read_role
):
    """BFF-VIEWER-REGISTRY-READ-20261106:
    A genuine strict-mode same-tenant ``viewer`` or ``view_only`` principal can
    read Registry GET surfaces (entries, strategy-specs including
    persona_binding / persona_binding_counts / strategy_summary_page views,
    strategy-artifacts, lineage, and allocation-policy-artifacts) with actual
    owner data, while missing/invalid credentials (401), unmapped roles (403),
    cross-tenant X-Tenant-Id selectors (403), and cross-tenant entry reads (403)
    remain denied.
    """
    from .service import _REGISTRY_READ_ROLES, _REGISTRY_WRITE_ROLES
    from .test_service import _cross_tenant_artifact_registration, _minimal_strategy_spec

    assert _REGISTRY_WRITE_ROLES == ("operator", "registry-writer", "admin")
    assert read_role in _REGISTRY_READ_ROLES
    assert read_role not in _REGISTRY_WRITE_ROLES

    op_token_a = _jwt(sub="operator-seed-a", roles=["operator"], tenant="tenant-a")
    op_token_b = _jwt(sub="operator-seed-b", roles=["operator"], tenant="tenant-b")

    entry_a = strict_client.post(
        "/api/registry/entries",
        json={**_ENTRY_PAYLOAD, "strategy_id": "strat-viewer-a", "lineage": {"source_run_ids": ["run-vw-a"]}},
        headers=_bearer(op_token_a),
    )
    assert entry_a.status_code == 200, entry_a.text
    entry_a_id = entry_a.json()["entry"]["registry_id"]

    entry_b = strict_client.post(
        "/api/registry/entries",
        json={**_ENTRY_PAYLOAD, "strategy_id": "strat-viewer-b", "lineage": {"source_run_ids": ["run-vw-b"]}},
        headers=_bearer(op_token_b),
    )
    assert entry_b.status_code == 200, entry_b.text
    entry_b_id = entry_b.json()["entry"]["registry_id"]

    spec_a = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": "reg-spec-viewer-a",
            "strategy_id": "strat-viewer-a",
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-vw-a"]},
            "metadata": {"persona_ids": ["persona-tw-alpha", "persona-us-core"], "title": "Viewer Tenant A Spec"},
            "strategy_spec": _minimal_strategy_spec("strat-viewer-a"),
        },
        headers=_bearer(op_token_a),
    )
    assert spec_a.status_code == 200, spec_a.text

    spec_b = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": "reg-spec-viewer-b",
            "strategy_id": "strat-viewer-b",
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-vw-b"]},
            "metadata": {"persona_ids": ["persona-foreign"], "title": "Foreign Tenant B Spec"},
            "strategy_spec": _minimal_strategy_spec("strat-viewer-b"),
        },
        headers=_bearer(op_token_b),
    )
    assert spec_b.status_code == 200, spec_b.text

    from .models import ArtifactState, ArtifactType, Lineage, RegistryEntryCreate, StorageBackend, StorageRef
    from .storage import get_store

    art_a = strict_client.post(
        "/api/registry/strategy-artifacts",
        json=_cross_tenant_artifact_registration(artifact_id="sa-viewer-tenant-a"),
        headers=_bearer(op_token_a),
    )
    assert art_a.status_code == 200, art_a.text
    art_a_strategy_id = art_a.json()["entry"]["strategy_id"]

    get_store().create(
        RegistryEntryCreate(
            artifact_type=ArtifactType.EXECUTION_BUNDLE,
            strategy_id="strat-viewer-b",
            version="1.0.0",
            artifact_state=ArtifactState.CANDIDATE,
            lineage=Lineage(source_run_ids=["run-vw-b"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_artifact"),
            checksum="sha256:sa-viewer-tenant-b",
            metadata={"strategy_artifact": {"artifact_id": "sa-viewer-tenant-b"}},
        ),
        "sa-viewer-tenant-b",
        actor={"tenant": "tenant-b"},
    )

    alloc_a = strict_client.post(
        "/api/registry/allocation-policy-artifacts",
        json={
            "registry_id": "reg-alloc-viewer-a",
            "version": "1.0.0",
            "allocation_policy_artifact": {
                "artifact_id": "alloc-viewer-a",
                "capital_pool_id": "pool-viewer-a",
                "scope_ref": "scope:tw",
                "sponsor_persona_id": "persona-tw-alpha",
                "synthesis_method": "single_proposal",
                "target_weights": {"2330.TW": 0.6, "2317.TW": 0.4},
                "created_at": "2026-11-06T00:00:00Z",
                "provenance_refs": ["prop-vw-a-1"],
                "conflict_resolution_log_id": "crl-vw-a-1",
            },
        },
        headers=_bearer(op_token_a),
    )
    assert alloc_a.status_code == 200, alloc_a.text

    viewer_token = _jwt(sub=f"{read_role}-actor-a", roles=[read_role], tenant="tenant-a")
    viewer_headers = {**_bearer(viewer_token), "X-Tenant-Id": "tenant-a"}

    # 1. Entry & strategy-scoped GET routes succeed for same-tenant viewer / view_only
    r_entry = strict_client.get(f"/api/registry/entries/{entry_a_id}", headers=viewer_headers)
    assert r_entry.status_code == 200, r_entry.text
    assert r_entry.json()["entry"]["registry_id"] == entry_a_id

    r_strat_entries = strict_client.get("/api/registry/strategies/strat-viewer-a/entries", headers=viewer_headers)
    assert r_strat_entries.status_code == 200, r_strat_entries.text
    assert {e["entry"]["registry_id"] for e in r_strat_entries.json()} == {entry_a_id, "reg-spec-viewer-a"}

    # 2. StrategySpec list + scoped views (persona_binding, persona_binding_counts, strategy_summary_page)
    r_specs_full = strict_client.get("/api/registry/strategy-specs", headers=viewer_headers)
    assert r_specs_full.status_code == 200, r_specs_full.text
    assert [e["entry"]["registry_id"] for e in r_specs_full.json()] == ["reg-spec-viewer-a"]

    r_pb = strict_client.get("/api/registry/strategy-specs?view=persona_binding", headers=viewer_headers)
    assert r_pb.status_code == 200, r_pb.text
    assert [e["entry"]["registry_id"] for e in r_pb.json()] == ["reg-spec-viewer-a"]
    assert r_pb.json()[0]["entry"]["metadata"]["persona_ids"] == ["persona-tw-alpha", "persona-us-core"]

    r_pbc = strict_client.get("/api/registry/strategy-specs?view=persona_binding_counts", headers=viewer_headers)
    assert r_pbc.status_code == 200, r_pbc.text
    assert r_pbc.json() == {"persona-tw-alpha": 1, "persona-us-core": 1}

    r_ssp = strict_client.get("/api/registry/strategy-specs?view=strategy_summary_page&page_size=20", headers=viewer_headers)
    assert r_ssp.status_code == 200, r_ssp.text
    ssp_body = r_ssp.json()
    assert ssp_body["total"] == 1
    assert ssp_body["items"][0]["strategy_id"] == "strat-viewer-a"
    assert ssp_body["items"][0]["current_spec_version_id"] == "reg-spec-viewer-a"
    assert ssp_body["items"][0]["persona_ids"] == ["persona-tw-alpha", "persona-us-core"]

    r_spec_detail = strict_client.get("/api/registry/strategy-specs/reg-spec-viewer-a", headers=viewer_headers)
    assert r_spec_detail.status_code == 200, r_spec_detail.text
    assert r_spec_detail.json()["entry"]["registry_id"] == "reg-spec-viewer-a"

    r_strat_specs = strict_client.get("/api/registry/strategies/strat-viewer-a/strategy-specs", headers=viewer_headers)
    assert r_strat_specs.status_code == 200, r_strat_specs.text
    assert [e["entry"]["registry_id"] for e in r_strat_specs.json()] == ["reg-spec-viewer-a"]

    # 3. StrategyArtifact, Lineage, and AllocationPolicyArtifact GET routes succeed
    r_art_list = strict_client.get("/api/registry/strategy-artifacts", headers=viewer_headers)
    assert r_art_list.status_code == 200, r_art_list.text
    art_ids = {item["registry_id"] for item in r_art_list.json()["items"]}
    assert "sa-viewer-tenant-a" in art_ids
    assert "sa-viewer-tenant-b" not in art_ids

    r_art_detail = strict_client.get("/api/registry/strategy-artifacts/sa-viewer-tenant-a", headers=viewer_headers)
    assert r_art_detail.status_code == 200, r_art_detail.text

    r_strat_arts = strict_client.get(
        f"/api/registry/strategies/{art_a_strategy_id}/strategy-artifacts",
        headers=viewer_headers,
    )
    assert r_strat_arts.status_code == 200, r_strat_arts.text
    assert any(e["entry"]["registry_id"] == "sa-viewer-tenant-a" for e in r_strat_arts.json())
    assert not any(e["entry"]["registry_id"] == "sa-viewer-tenant-b" for e in r_strat_arts.json())

    r_lineage = strict_client.get("/api/registry/lineage?root_id=reg-spec-viewer-a", headers=viewer_headers)
    assert r_lineage.status_code == 200, r_lineage.text
    lineage_ids = {n["id"] for n in r_lineage.json()["nodes"]}
    assert "reg-spec-viewer-a" in lineage_ids
    assert "reg-spec-viewer-b" not in lineage_ids
    assert "sa-viewer-tenant-b" not in lineage_ids

    r_alloc_detail = strict_client.get("/api/registry/allocation-policy-artifacts/reg-alloc-viewer-a", headers=viewer_headers)
    assert r_alloc_detail.status_code == 200, r_alloc_detail.text

    r_alloc_list = strict_client.get("/api/registry/pools/pool-viewer-a/allocation-policy-artifacts", headers=viewer_headers)
    assert r_alloc_list.status_code == 200, r_alloc_list.text
    assert [e["entry"]["registry_id"] for e in r_alloc_list.json()] == ["reg-alloc-viewer-a"]

    # 4. Cross-tenant selectors and cross-tenant entry reads remain denied (403)
    cross_tenant_headers = {**_bearer(viewer_token), "X-Tenant-Id": "tenant-b"}
    for path in (
        "/api/registry/strategy-specs",
        "/api/registry/strategy-specs?view=persona_binding",
        "/api/registry/strategy-specs?view=persona_binding_counts",
        "/api/registry/strategy-specs?view=strategy_summary_page",
        "/api/registry/strategies/strat-viewer-a/strategy-specs",
        "/api/registry/strategy-artifacts",
    ):
        resp_cross_hdr = strict_client.get(path, headers=cross_tenant_headers)
        assert resp_cross_hdr.status_code == 403, (path, resp_cross_hdr.text)

    for foreign_path in (
        f"/api/registry/entries/{entry_b_id}",
        "/api/registry/entries/sa-viewer-tenant-b",
        "/api/registry/strategy-specs/reg-spec-viewer-b",
    ):
        resp_foreign = strict_client.get(foreign_path, headers=viewer_headers)
        assert resp_foreign.status_code == 403, (foreign_path, resp_foreign.text)

    viewer_b_headers = {
        **_bearer(_jwt(sub=f"{read_role}-actor-b", roles=[read_role], tenant="tenant-b")),
        "X-Tenant-Id": "tenant-b",
    }
    resp_foreign_art = strict_client.get("/api/registry/strategy-artifacts/sa-viewer-tenant-a", headers=viewer_b_headers)
    assert resp_foreign_art.status_code == 403, resp_foreign_art.text

    # 5. Missing/invalid credentials (401) and non-read roles (403) remain denied
    assert strict_client.get("/api/registry/strategy-specs?view=persona_binding_counts").status_code == 401
    assert strict_client.get(
        "/api/registry/strategy-specs?view=persona_binding_counts",
        headers={"Authorization": "Bearer invalid.jwt.token"},
    ).status_code == 401
    unmapped_token = _jwt(sub="auditor-1", roles=["auditor"], tenant="tenant-a")
    assert strict_client.get(
        "/api/registry/strategy-specs?view=persona_binding_counts",
        headers=_bearer(unmapped_token),
    ).status_code == 403


@pytest.mark.parametrize("read_role", ["viewer", "view_only"])
def test_strict_mode_every_registry_mutation_rejects_viewer_and_view_only_and_keeps_write_roles_narrow(
    strict_client, read_role
):
    """BFF-VIEWER-REGISTRY-READ-20261106:
    Every Registry mutation route rejects ``viewer`` and ``view_only`` with 403
    and ``_REGISTRY_WRITE_ROLES`` remains strictly ``("operator", "registry-writer", "admin")``.
    """
    from .service import _REGISTRY_WRITE_ROLES
    from .test_service import _cross_tenant_artifact_registration, _minimal_strategy_spec

    assert _REGISTRY_WRITE_ROLES == ("operator", "registry-writer", "admin")

    op_token = _jwt(sub="operator-mut-seed", roles=["operator"], tenant="tenant-a")
    entry_resp = strict_client.post(
        "/api/registry/entries",
        json={**_ENTRY_PAYLOAD, "strategy_id": "strat-mut-guard", "lineage": {"source_run_ids": ["run-mut-1"]}},
        headers=_bearer(op_token),
    )
    assert entry_resp.status_code == 200, entry_resp.text
    entry_id = entry_resp.json()["entry"]["registry_id"]

    spec_resp = strict_client.post(
        "/api/registry/strategy-specs",
        json={
            "registry_id": "reg-spec-mut-guard",
            "strategy_id": "strat-mut-guard",
            "version": "1.0.0",
            "lineage": {"source_run_ids": ["run-mut-1"]},
            "strategy_spec": _minimal_strategy_spec("strat-mut-guard"),
        },
        headers=_bearer(op_token),
    )
    assert spec_resp.status_code == 200, spec_resp.text

    art_resp = strict_client.post(
        "/api/registry/strategy-artifacts",
        json=_cross_tenant_artifact_registration(artifact_id="sa-mut-guard"),
        headers=_bearer(op_token),
    )
    assert art_resp.status_code == 200, art_resp.text

    alloc_resp = strict_client.post(
        "/api/registry/allocation-policy-artifacts",
        json={
            "registry_id": "reg-alloc-mut-guard",
            "version": "1.0.0",
            "allocation_policy_artifact": {
                "artifact_id": "alloc-mut-guard",
                "capital_pool_id": "pool-mut-guard",
                "scope_ref": "scope:tw",
                "sponsor_persona_id": "persona-tw-alpha",
                "synthesis_method": "single_proposal",
                "target_weights": {"2330.TW": 1.0},
                "created_at": "2026-11-06T00:00:00Z",
                "provenance_refs": ["prop-mut-1"],
                "conflict_resolution_log_id": "crl-mut-1",
            },
        },
        headers=_bearer(op_token),
    )
    assert alloc_resp.status_code == 200, alloc_resp.text

    viewer_headers = _bearer(_jwt(sub=f"{read_role}-mut-attempt", roles=[read_role], tenant="tenant-a"))
    mutation_cases = [
        ("POST", "/api/registry/entries", _ENTRY_PAYLOAD),
        ("PATCH", f"/api/registry/entries/{entry_id}/metadata", {"expected_metadata": None, "metadata": {"note": "denied"}}),
        ("POST", f"/api/registry/entries/{entry_id}/advance", {"target_state": "candidate", "expected_artifact_state": "draft"}),
        ("PUT", f"/api/registry/entries/{entry_id}/deployment-summary", {"current_stage": "paper"}),
        (
            "POST",
            "/api/registry/strategy-specs",
            {
                "strategy_id": "strat-viewer-write-denied",
                "version": "1.0.0",
                "lineage": {"source_run_ids": ["run-1"]},
                "strategy_spec": _minimal_strategy_spec("strat-viewer-write-denied"),
            },
        ),
        (
            "POST",
            "/api/registry/strategy-specs/reg-spec-mut-guard/advance",
            {"target_state": "candidate", "expected_artifact_state": "draft"},
        ),
        ("POST", "/api/registry/strategy-artifacts", _cross_tenant_artifact_registration(artifact_id="sa-viewer-write-denied")),
        (
            "POST",
            "/api/registry/strategy-artifacts/sa-mut-guard/mutate",
            {
                "new_artifact_id": "sa-mut-guard-v2",
                "new_version": "1.2.0",
                "parameter_updates": {"momentum_threshold": 0.04},
                "source_run_ids": ["run-mut-2"],
            },
        ),
        (
            "POST",
            "/api/registry/strategy-artifacts/sa-mut-guard/advance",
            {"target_state": "retired", "expected_artifact_state": "candidate"},
        ),
        (
            "POST",
            "/api/registry/allocation-policy-artifacts",
            {
                "version": "1.0.0",
                "allocation_policy_artifact": {
                    "artifact_id": "alloc-viewer-denied",
                    "capital_pool_id": "pool-mut-guard",
                    "scope_ref": "scope:tw",
                    "sponsor_persona_id": "persona-tw-alpha",
                    "synthesis_method": "single_proposal",
                    "target_weights": {"2330.TW": 1.0},
                    "created_at": "2026-11-06T00:00:00Z",
                    "provenance_refs": ["prop-mut-2"],
                    "conflict_resolution_log_id": "crl-mut-2",
                },
            },
        ),
        (
            "POST",
            "/api/registry/allocation-policy-artifacts/reg-alloc-mut-guard/advance",
            {"target_state": "retired", "expected_artifact_state": "candidate"},
        ),
    ]

    for method, path, body in mutation_cases:
        resp = strict_client.request(method, path, json=body, headers=viewer_headers)
        assert resp.status_code == 403, (read_role, method, path, resp.status_code, resp.text)
        assert "not authorized" in resp.json()["detail"].lower()


def test_registry_auth_env_forwards_oidc_and_role_map_fallbacks(monkeypatch):
    from .service import _registry_auth_env

    monkeypatch.setenv("PANTHEON_BFF_JWKS_URI", "https://idp.example/bff/jwks")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", "https://idp.example/.well-known/openid-configuration")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_ISSUER", "https://idp.example/issuer")
    monkeypatch.setenv("PANTHEON_BFF_OIDC_AUDIENCE", "pantheon-stage-aud")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_CLAIMS", "roles,groups,realm_access.roles")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP", "pantheon-operator=operator;pantheon-viewer=viewer")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED", "true")

    env = _registry_auth_env()
    assert env["PANTHEON_RUNTIME_JWKS_URI"] == "https://idp.example/bff/jwks"
    assert env["PANTHEON_RUNTIME_OIDC_DISCOVERY_URL"] == "https://idp.example/.well-known/openid-configuration"
    assert env["PANTHEON_RUNTIME_OIDC_ISSUER"] == "https://idp.example/issuer"
    assert env["PANTHEON_RUNTIME_OIDC_AUDIENCE"] == "pantheon-stage-aud"
    assert env["PANTHEON_RUNTIME_ROLE_CLAIMS"] == "roles,groups,realm_access.roles"
    assert env["PANTHEON_RUNTIME_ROLE_MAP"] == "pantheon-operator=operator;pantheon-viewer=viewer"
    assert env["PANTHEON_RUNTIME_ROLE_MAP_MODE"] == "strict"
    assert env["PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED"] == "true"

    monkeypatch.setenv("PANTHEON_RUNTIME_ROLE_MAP", "runtime-op=operator")
    assert _registry_auth_env()["PANTHEON_RUNTIME_ROLE_MAP"] == "runtime-op=operator"
    monkeypatch.setenv("PANTHEON_REGISTRY_ROLE_MAP", "registry-op=operator")
    assert _registry_auth_env()["PANTHEON_RUNTIME_ROLE_MAP"] == "registry-op=operator"


def test_strict_mode_external_role_map_allows_mapped_operator_and_viewer(strict_client, monkeypatch):
    monkeypatch.setenv(
        "PANTHEON_RUNTIME_ROLE_MAP",
        "pantheon-operator=operator;pantheon-viewer=viewer",
    )
    monkeypatch.setenv("PANTHEON_RUNTIME_ROLE_MAP_MODE", "strict")

    op_headers = _bearer(_jwt(sub="idp-op-1", roles=["pantheon-operator"], tenant="tenant-a"))
    list_resp = strict_client.get(
        "/api/registry/strategy-specs?exclude_state=draft&page_size=20",
        headers=op_headers,
    )
    assert list_resp.status_code == 200, list_resp.text

    viewer_headers = _bearer(_jwt(sub="idp-viewer-1", roles=["pantheon-viewer"], tenant="tenant-a"))
    viewer_list = strict_client.get(
        "/api/registry/strategy-specs?exclude_state=draft&page_size=20",
        headers=viewer_headers,
    )
    assert viewer_list.status_code == 200, viewer_list.text

    viewer_write = strict_client.post("/api/registry/entries", json=_ENTRY_PAYLOAD, headers=viewer_headers)
    assert viewer_write.status_code == 403

    unmapped_headers = _bearer(_jwt(sub="idp-unmapped-1", roles=["external-unmapped"], tenant="tenant-a"))
    unmapped_resp = strict_client.get(
        "/api/registry/strategy-specs?exclude_state=draft&page_size=20",
        headers=unmapped_headers,
    )
    assert unmapped_resp.status_code == 403


def test_dual_mode_shared_secret_preserves_canonical_internal_roles():
    from services.runtime_auth_inbound import AuthError, validate_request_auth

    dual_env = {
        "PANTHEON_RUNTIME_AUTH_MODE": "strict",
        "PANTHEON_RUNTIME_JWT_SECRET": _JWT_SECRET,
        "PANTHEON_RUNTIME_JWT_ISSUER": "pantheon-runtime-authority",
        "PANTHEON_RUNTIME_JWT_AUDIENCE": "pantheon-runtime",
        "PANTHEON_RUNTIME_OIDC_ISSUER": "https://securetoken.google.com/stage-proj",
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": "stage-proj",
        "PANTHEON_RUNTIME_JWKS_URI": "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com",
        "PANTHEON_RUNTIME_ROLE_CLAIMS": "roles,role",
        "PANTHEON_RUNTIME_ROLE_MAP": "pantheon-operator=operator;pantheon-viewer=viewer",
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": "strict",
        "PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED": "true",
    }
    internal_token = encode_jwt_hs256(
        {
            "sub": "paper-signal-producer",
            "iss": "pantheon-runtime-authority",
            "aud": "pantheon-runtime",
            "roles": ["operator"],
            "exp": int(time.time()) + 3600,
        },
        secret=_JWT_SECRET,
    )
    ctx = validate_request_auth(
        authorization=f"Bearer {internal_token}",
        required_roles=("operator", "admin", "viewer"),
        env=dual_env,
        allow_shared_secret=True,
    )
    assert ctx.actor_id == "paper-signal-producer"
    assert "operator" in ctx.roles

    with pytest.raises(AuthError) as exc_info:
        validate_request_auth(
            authorization=f"Bearer {internal_token}",
            required_roles=("operator", "admin", "viewer"),
            env=dual_env,
            allow_shared_secret=False,
        )
    assert exc_info.value.status_code == 401


def test_registry_oidc_rs256_mapped_groups_and_dot_path_roles_succeed_while_unmapped_or_missing_fail_closed(
    strict_client, monkeypatch, tmp_path
):
    import base64
    import json
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from services.runtime_auth_inbound import _JWKS_CACHE

    _JWKS_CACHE.clear()
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_nums = rsa_key.public_key().public_numbers()
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    int_b64 = lambda val: b64(val.to_bytes((val.bit_length() + 7) // 8, "big"))
    kid = "registry-oidc-key-1"
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "kty": "RSA",
                        "kid": kid,
                        "use": "sig",
                        "alg": "RS256",
                        "n": int_b64(pub_nums.n),
                        "e": int_b64(pub_nums.e),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def _mint_rs256(payload: dict) -> str:
        hdr_b64 = b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": kid}).encode("utf-8"))
        pay_b64 = b64(json.dumps(payload).encode("utf-8"))
        sig = rsa_key.sign(f"{hdr_b64}.{pay_b64}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{hdr_b64}.{pay_b64}.{b64(sig)}"

    oidc_iss = "https://securetoken.google.com/pantheon-stage-registry"
    oidc_aud = "pantheon-stage-registry"
    monkeypatch.setenv("PANTHEON_REGISTRY_JWKS_URI", jwks_path.as_uri())
    monkeypatch.setenv("PANTHEON_REGISTRY_OIDC_ISSUER", oidc_iss)
    monkeypatch.setenv("PANTHEON_REGISTRY_OIDC_AUDIENCE", oidc_aud)
    monkeypatch.setenv("PANTHEON_REGISTRY_ROLE_CLAIMS", "roles,groups,realm_access.roles")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP", "pantheon-operator=operator;pantheon-viewer=viewer")
    monkeypatch.setenv("PANTHEON_BFF_ROLE_MAP_MODE", "strict")
    monkeypatch.setenv("PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED", "true")

    now = int(time.time())
    mapped_group_op = _mint_rs256(
        {
            "iss": oidc_iss,
            "aud": oidc_aud,
            "sub": "oidc-reg-op-1",
            "tenant_id": "tenant-a",
            "email_verified": True,
            "groups": ["pantheon-operator"],
            "iat": now,
            "exp": now + 600,
        }
    )
    create_resp = strict_client.post(
        "/api/registry/entries",
        json=_ENTRY_PAYLOAD,
        headers=_bearer(mapped_group_op),
    )
    assert create_resp.status_code == 200, create_resp.text
    assert create_resp.json()["entry"]["last_actor"]["actor_id"] == "oidc-reg-op-1"
    assert create_resp.json()["entry"]["last_actor"]["tenant"] == "tenant-a"

    mapped_dot_viewer = _mint_rs256(
        {
            "iss": oidc_iss,
            "aud": oidc_aud,
            "sub": "oidc-reg-viewer-1",
            "tenant_id": "tenant-a",
            "email_verified": True,
            "realm_access": {"roles": ["pantheon-viewer"]},
            "iat": now,
            "exp": now + 600,
        }
    )
    read_resp = strict_client.get(
        "/api/registry/strategy-specs?exclude_state=draft&page_size=20",
        headers=_bearer(mapped_dot_viewer),
    )
    assert read_resp.status_code == 200, read_resp.text

    viewer_write_resp = strict_client.post(
        "/api/registry/entries",
        json=_ENTRY_PAYLOAD,
        headers=_bearer(mapped_dot_viewer),
    )
    assert viewer_write_resp.status_code == 403

    unmapped_group_token = _mint_rs256(
        {
            "iss": oidc_iss,
            "aud": oidc_aud,
            "sub": "oidc-reg-unmapped",
            "tenant_id": "tenant-a",
            "email_verified": True,
            "groups": ["external-unmapped-group"],
            "iat": now,
            "exp": now + 600,
        }
    )
    unmapped_resp = strict_client.get(
        "/api/registry/strategy-specs?exclude_state=draft&page_size=20",
        headers=_bearer(unmapped_group_token),
    )
    assert unmapped_resp.status_code == 403

    missing_role_token = _mint_rs256(
        {
            "iss": oidc_iss,
            "aud": oidc_aud,
            "sub": "oidc-reg-roleless",
            "tenant_id": "tenant-a",
            "email_verified": True,
            "iat": now,
            "exp": now + 600,
        }
    )
    monkeypatch.setenv("PANTHEON_REGISTRY_DEFAULT_ROLE", "operator")
    for method, path, kwargs in (
        ("get", "/api/registry/strategy-specs?exclude_state=draft&page_size=20", {}),
        ("post", "/api/registry/entries", {"json": _ENTRY_PAYLOAD}),
    ):
        resp_missing = getattr(strict_client, method)(
            path, headers=_bearer(missing_role_token), **kwargs
        )
        assert resp_missing.status_code == 403, f"{method} {path}: {resp_missing.text}"
    _JWKS_CACHE.clear()
