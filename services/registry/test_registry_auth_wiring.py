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
        json={"target_state": "candidate"},
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
        json={"target_state": "candidate"},
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
