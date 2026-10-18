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
    - GET /api/registry/strategies/{strategy_id}/deployment-view fails closed with 503 when
      configured runtime-manager or deployment owner endpoints are unreachable or unauthenticated
      (401/403), never falling back to entry.deployment_summary or synthesizing current_stage='none'.
    """
    import io
    import json
    from pathlib import Path
    import urllib.error
    import urllib.request
    from services.governance.test_approval_authority import (
        advance_registry_http,
        configure_registry_unit_auth,
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

    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_API_URL", "http://127.0.0.1:9")

    # 1. Unreachable runtime-manager -> 503
    resp_unreachable = strict_client.get(
        "/api/registry/strategies/auth-wiring-strat/deployment-view",
        headers=headers,
    )
    assert resp_unreachable.status_code == 503, resp_unreachable.text

    # 2. Unauthenticated runtime-manager (HTTP 401) -> 503
    def _urlopen_runtime_401(req, timeout=5.0):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_runtime_401)
    resp_rm_401 = strict_client.get(
        "/api/registry/strategies/auth-wiring-strat/deployment-view",
        headers=headers,
    )
    assert resp_rm_401.status_code == 503, resp_rm_401.text

    # 3. Authenticated runtime-manager returning active binding, but unauthenticated deployment (HTTP 403) -> 503
    class _FakeResp:
        def __init__(self, payload: dict):
            self._bytes = json.dumps(payload).encode("utf-8")

        def read(self) -> bytes:
            return self._bytes

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _urlopen_deployment_403(req, timeout=5.0):
        if req.full_url.endswith("/api/runtime-bindings"):
            assert req.headers.get("X-tenant-id") == "tenant-a"
            assert req.headers.get("Authorization") == f"Bearer {token}"
            return _FakeResp(
                {
                    "bindings": [
                        {
                            "binding_id": "rb-auth-1",
                            "artifact_id": rid,
                            "artifact_version": "1.0.0",
                            "deployment_mode": "paper",
                            "plan_id": "plan-auth-1",
                            "status": "active",
                            "effective_at": "2026-10-18T00:00:00Z",
                            "metadata": {"tenant_id": "tenant-a", "strategy_id": "auth-wiring-strat"},
                        }
                    ]
                }
            )
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=io.BytesIO(b"{}"))

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_deployment_403)
    resp_dep_403 = strict_client.get(
        "/api/registry/strategies/auth-wiring-strat/deployment-view",
        headers=headers,
    )
    assert resp_dep_403.status_code == 503, resp_dep_403.text

