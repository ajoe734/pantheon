from __future__ import annotations

import time

from fastapi.testclient import TestClient

from services.runtime_auth_inbound import encode_jwt_hs256
from test_http_service import _load_service_module


SECRET = "training-session-inbound-authority-test-secret"


def _configure_strict(monkeypatch) -> None:
    monkeypatch.delenv("TRAINING_SESSION_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("TRAINING_SESSION_AUTH_MODE", "strict")
    monkeypatch.setenv("TRAINING_SESSION_JWT_SECRET", SECRET)
    monkeypatch.setenv("TRAINING_SESSION_MFA_REQUIRED", "true")
    monkeypatch.setenv(
        "TRAINING_SESSION_ALLOWED_CALLER_SERVICES",
        "control-plane-bff,training-session-preview-worker",
    )


def _token(
    *,
    service: str = "control-plane-bff",
    tenants: list[str] | None = None,
    mfa: bool = False,
    role: str | None = "training-service",
) -> str:
    claims = {
        "sub": service,
        "service": service,
        "tenant_ids": tenants or ["tenant-a"],
        "delegated_actor_id": "operator-a",
        "exp": time.time() + 3600,
    }
    if role is not None:
        claims["roles"] = [role]
    if mfa:
        claims["amr"] = ["pwd", "mfa"]
    return encode_jwt_hs256(claims, secret=SECRET)


def _headers(
    *,
    tenant: str = "tenant-a",
    service: str = "control-plane-bff",
    token: str | None = None,
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token or _token(service=service, tenants=[tenant])}",
        "X-Tenant-Id": tenant,
        "X-Pantheon-Service": service,
    }


def test_teaching_mutation_requires_bearer_service_and_tenant(monkeypatch) -> None:
    module = _load_service_module()
    _configure_strict(monkeypatch)
    client = TestClient(module.app)

    missing_bearer = client.post(
        "/api/training/sessions",
        json={"persona_id": "persona-a", "objective": "secure teaching"},
        headers={"X-Tenant-Id": "tenant-a", "X-Pantheon-Service": "control-plane-bff"},
    )
    missing_tenant = client.post(
        "/api/training/sessions",
        json={"persona_id": "persona-a", "objective": "secure teaching"},
        headers={
            "Authorization": f"Bearer {_token()}",
            "X-Pantheon-Service": "control-plane-bff",
        },
    )
    wrong_service = client.post(
        "/api/training/sessions",
        json={"persona_id": "persona-a", "objective": "secure teaching"},
        headers=_headers(service="control-plane-bff", token=_token(service="other-service")),
    )

    assert missing_bearer.status_code == 401
    assert missing_tenant.status_code == 400
    assert missing_tenant.json()["error"]["code"] == "TENANT_REQUIRED"
    assert wrong_service.status_code == 403
    assert wrong_service.json()["error"]["code"] == "ACTOR_SERVICE_MISMATCH"


def test_strict_jwt_requires_explicit_authorized_training_role(monkeypatch) -> None:
    module = _load_service_module()
    _configure_strict(monkeypatch)
    client = TestClient(module.app)

    missing_role = client.post(
        "/api/training/sessions",
        json={"persona_id": "persona-a", "objective": "secure teaching"},
        headers=_headers(token=_token(role=None)),
    )
    wrong_role = client.post(
        "/api/training/sessions",
        json={"persona_id": "persona-a", "objective": "secure teaching"},
        headers=_headers(token=_token(role="viewer")),
    )

    assert missing_role.status_code == 403
    assert missing_role.json()["error"]["code"] == "AUTH_FORBIDDEN"
    assert wrong_role.status_code == 403
    assert wrong_role.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_teaching_records_are_bound_to_verified_tenant_and_service(monkeypatch) -> None:
    module = _load_service_module()
    _configure_strict(monkeypatch)
    client = TestClient(module.app)

    spoofed_actor = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-a",
            "objective": "tenant-safe teaching",
            "actor_id": "untrusted-body-actor",
        },
        headers=_headers(),
    )
    assert spoofed_actor.status_code == 403

    created = client.post(
        "/api/training/sessions",
        json={
            "persona_id": "persona-a",
            "objective": "tenant-safe teaching",
            "actor_id": "operator-a",
        },
        headers=_headers(),
    )
    assert created.status_code == 201, created.text
    session = created.json()
    assert session["tenant_id"] == "tenant-a"
    assert session["opened_by"] == "operator-a"
    assert session["actor_context"]["actor_service"] == "control-plane-bff"
    assert session["actor_context"]["authenticated_actor_id"] == "control-plane-bff"

    tenant_b_headers = _headers(
        tenant="tenant-b",
        token=_token(tenants=["tenant-b"]),
    )
    cross_tenant_read = client.get(
        f"/api/training/sessions/{session['session_id']}",
        headers=tenant_b_headers,
    )
    tenant_b_list = client.get("/api/training/sessions", headers=tenant_b_headers)

    assert cross_tenant_read.status_code == 404
    assert tenant_b_list.status_code == 200
    assert tenant_b_list.json() == []


def test_replay_commit_requires_verified_mfa_before_route_execution(monkeypatch) -> None:
    module = _load_service_module()
    _configure_strict(monkeypatch)
    client = TestClient(module.app)

    without_mfa = client.post(
        "/api/training/replays/not-visible/commit",
        json={},
        headers={**_headers(token=_token(mfa=False)), "Idempotency-Key": "commit-1"},
    )
    malformed_mfa = client.post(
        "/api/training/replays/not-visible/commit",
        json={},
        headers={
            **_headers(token=_token(mfa=False)),
            "X-MFA-Token": "not-an-otp",
            "Idempotency-Key": "commit-2",
        },
    )
    well_formed_unverified_mfa = client.post(
        "/api/training/replays/not-visible/commit",
        json={},
        headers={
            **_headers(token=_token(mfa=False)),
            "X-MFA-Token": "123456",
            "Idempotency-Key": "commit-unverified",
        },
    )
    verified_mfa = client.post(
        "/api/training/replays/not-visible/commit",
        json={},
        headers={
            **_headers(token=_token(mfa=True)),
            "Idempotency-Key": "commit-3",
        },
    )

    assert without_mfa.status_code == 401
    assert without_mfa.json()["error"]["code"] == "MFA_REQUIRED"
    assert malformed_mfa.status_code == 400
    assert malformed_mfa.json()["error"]["code"] == "MFA_VALIDATION_FAILED"
    assert well_formed_unverified_mfa.status_code == 401
    assert well_formed_unverified_mfa.json()["error"]["code"] == "MFA_NOT_VERIFIED"
    # Auth/MFA passed, so the normal resource boundary is now reached.
    assert verified_mfa.status_code == 404
    readiness = client.get("/readyz")
    assert readiness.status_code == 503
    failures = readiness.json()["dependencies"]["functional"]["failures"]
    assert failures[-1]["operation"] == "persona_commit"
    assert failures[-1]["status"] == "failed"


def test_commit_and_discard_reject_well_formed_but_unverified_mfa(monkeypatch) -> None:
    module = _load_service_module()
    _configure_strict(monkeypatch)
    client = TestClient(module.app)

    for decision in ("commit", "discard"):
        response = client.post(
            f"/api/training/replays/not-visible/{decision}",
            json={},
            headers={
                **_headers(token=_token(mfa=False)),
                "X-MFA-Token": "654321",
                "Idempotency-Key": f"{decision}-unverified",
            },
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "MFA_NOT_VERIFIED"


def test_caller_supplied_otp_is_not_recorded_as_verified_mfa(monkeypatch) -> None:
    module = _load_service_module()
    _configure_strict(monkeypatch)
    client = TestClient(module.app)

    response = client.post(
        "/api/training/sessions",
        json={"persona_id": "persona-a", "objective": "do not trust raw otp"},
        headers={
            **_headers(token=_token(mfa=False)),
            "X-MFA-Token": "123456",
        },
    )

    assert response.status_code == 201
    assert response.json()["actor_context"]["mfa_verified"] is False


def test_readiness_degrades_when_strict_inbound_verifier_is_missing(monkeypatch) -> None:
    module = _load_service_module()
    monkeypatch.delenv("TRAINING_SESSION_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("TRAINING_SESSION_AUTH_MODE", "strict")
    for name in (
        "TRAINING_SESSION_JWT_SECRET",
        "TRAINING_SESSION_JWKS_URI",
        "TRAINING_SESSION_OIDC_DISCOVERY_URL",
        "PANTHEON_RUNTIME_JWT_SECRET",
        "PANTHEON_RUNTIME_JWKS_URI",
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    readiness = TestClient(module.app).get("/readyz")

    assert readiness.status_code == 503
    dependency = readiness.json()["dependencies"]["inbound_authority"]
    assert dependency["status"] == "error"
    assert dependency["reason"] == "jwt_verifier_missing"
