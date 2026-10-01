"""Real Persona HTTP boundary and durable state; no hosted/live acceptance claim."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.control_plane.bff.ports.persona_write_owner import (
    PersonaRegistryHttpWritePort,
    PersonaWriteOwnerUnavailable,
    _PersonaHttpResponseError,
)
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid, ApprovalReader
from services.persona.write_owner import (
    HttpGovernanceApprovalVerifier,
    PersistentPersonaOwner,
    create_app,
)
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "isolated-persona-lifecycle-secret"
TENANT = "tenant-lifecycle"
PERSONA = "persona-lifecycle"


def bearer(*, actor="alice", tenant=TENANT, roles=("operator",)):
    return "Bearer " + encode_jwt_hs256({
        "sub": actor, "tenant_id": tenant, "roles": list(roles),
        "exp": 4102444800, "iss": "isolated", "aud": "isolated",
    }, secret=SECRET)


def evidence(**overrides):
    return ApprovalEvidence.model_validate({
        "decision_id": "decision-paper", "tenant_id": TENANT,
        "target_type": "persona_lifecycle_transition", "target_id": PERSONA,
        "target_version": "1", "decision_state": "decided", "decision": "approved",
        "actor_id": "independent-reviewer", "actor_role": "governance_reviewer",
        "decided_at": "2026-01-01T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z",
        "conditions": [], "controller_record_ref": "isolated-evidence",
        "authority_status": "authoritative", "recorded_at": "2026-01-01T00:00:00Z",
        "version": 1, "event_id": "isolated-event", "risk_level": "low",
        "owner_user_id": "proposer",
        "metadata": {
            "subject": {"persona_id": PERSONA, "from_state": "consultable", "to_state": "paper_owner"},
            "approvals": [{"actor_id": "independent-reviewer", "actor_role": "governance_reviewer"}],
        },
        **overrides,
    })


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    for prefix in ("PERSONA", "PANTHEON_BFF"):
        monkeypatch.setenv(f"{prefix}_AUTH_MODE", "strict")
        monkeypatch.setenv(f"{prefix}_JWT_SECRET", SECRET)
        monkeypatch.setenv(f"{prefix}_JWT_ISSUER", "isolated")
        monkeypatch.setenv(f"{prefix}_JWT_AUDIENCE", "isolated")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", TENANT)
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", TENANT)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", "isolated-provisioning-token")
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", "operator-bff")
    path = tmp_path / "personas.json"
    owner = PersistentPersonaOwner.from_json_path(path)
    records = {"decision-paper": evidence()}

    def read_decision(_reader, decision_id):
        value = records.get(decision_id)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise ApprovalInvalid("Governance denied exact decision read")
        return value

    monkeypatch.setattr(ApprovalReader, "get", read_decision)
    verifier = HttpGovernanceApprovalVerifier(base_url="http://isolated-governance", service_token="isolated")
    calls = []
    with TestClient(create_app(owner, governance_decision_verifier=verifier)) as client:
        def opener(request, *, timeout):
            headers = dict(request.header_items())
            response = client.request(request.get_method(), request.full_url,
                                      content=request.data, headers=headers)
            calls.append({"method": request.get_method(), "url": request.full_url,
                          "headers": headers, "body": json.loads(request.data) if request.data else None,
                          "status": response.status_code})
            if response.is_error:
                raise HTTPError(request.full_url, response.status_code, "owner rejected",
                                response.headers, io.BytesIO(response.content))
            return io.BytesIO(response.content)

        port = PersonaRegistryHttpWritePort(base_url="http://testserver", opener=opener)
        port.create_persona(persona_id=PERSONA, name="Isolated lifecycle", actor_id="alice",
                            metadata={"tenant_id": TENANT})
        port.update_persona(PERSONA, lifecycle_state="research_only")
        response = client.patch(f"/api/personas/{PERSONA}/lifecycle",
                                headers={"Authorization": bearer(actor="reviewer", roles=("governance_reviewer",))},
                                json={"actor_id": "reviewer", "target_state": "consultable"})
        assert response.status_code == 200, response.text
        calls.clear()
        yield SimpleNamespace(port=port, client=client, owner=owner, path=path,
                              records=records, calls=calls, tmp_path=tmp_path)


def advance(boundary, **overrides):
    return boundary.port.advance_lifecycle(PERSONA, **{
        "actor_id": "alice", "target_state": "paper_owner",
        "governance_decision_id": "decision-paper", "authorization": bearer(),
        **overrides,
    })


def test_http_port_preserves_original_caller_and_durable_owner_result(boundary):
    result = advance(boundary)
    assert boundary.calls[0]["headers"]["Authorization"] == bearer()
    assert boundary.calls[0]["method"] == "PATCH"
    assert boundary.calls[0]["body"] == {
        "actor_id": "alice", "target_state": "paper_owner", "governance_decision_id": "decision-paper",
    }
    fresh = PersistentPersonaOwner.from_json_path(boundary.path).get(PERSONA)
    assert fresh.model_dump(mode="json") == result
    assert fresh.tenant_id == TENANT
    assert fresh.updated_by == "alice"
    assert fresh.metadata["last_lifecycle_governance_decision_id"] == "decision-paper"


@pytest.mark.parametrize("overrides", [
    {"governance_decision_id": None},
    {"governance_decision_id": "unknown"},
    {"authorization": bearer(tenant="foreign")},
    {"actor_id": "spoofed"},
])
def test_http_port_preserves_owner_403_without_effect(boundary, overrides):
    with pytest.raises(_PersonaHttpResponseError) as failure:
        advance(boundary, **overrides)
    assert failure.value.status_code == 403
    assert PersistentPersonaOwner.from_json_path(boundary.path).get(PERSONA).lifecycle_state == "consultable"


@pytest.mark.parametrize("change", [
    {"target_id": "different-persona", "metadata": {"subject": {
        "persona_id": "different-persona", "from_state": "consultable", "to_state": "paper_owner",
    }}},
    {"tenant_id": "foreign"},
    {"expires_at": "2020-01-01T00:00:00Z"},
    {"revoked_at": "2026-02-01T00:00:00Z"}, {"decision": "rejected"},
])
def test_http_port_uses_actual_approval_evidence_validation(boundary, change):
    boundary.records["decision-paper"] = evidence(**change)
    with pytest.raises(_PersonaHttpResponseError) as failure:
        advance(boundary)
    assert failure.value.status_code == 403
    assert boundary.owner.get(PERSONA).lifecycle_state == "consultable"


def test_governance_unavailable_is_503_not_success(boundary):
    boundary.records["decision-paper"] = ConnectionError("isolated governance unavailable")
    with pytest.raises(_PersonaHttpResponseError) as failure:
        advance(boundary)
    assert failure.value.status_code == 503
    assert boundary.owner.get(PERSONA).lifecycle_state == "consultable"


def test_empty_caller_never_uses_provisioning_credential(boundary):
    with pytest.raises(_PersonaHttpResponseError) as failure:
        advance(boundary, authorization="")
    assert failure.value.status_code == 401
    assert boundary.calls == []


def test_lifecycle_does_not_require_a_provisioning_token(boundary):
    boundary.port._service_token = ""
    assert advance(boundary)["lifecycle_state"] == "paper_owner"


def test_missing_formal_tenant_stays_denied_not_backfilled(boundary):
    current = boundary.owner._records.get(PERSONA)
    unbound = {**current, "tenant_id": None}
    assert boundary.owner._records.compare_and_set(PERSONA, current, unbound)[0]
    with pytest.raises(_PersonaHttpResponseError) as failure:
        advance(boundary)
    assert failure.value.status_code == 403
    fresh = PersistentPersonaOwner.from_json_path(boundary.path).get(PERSONA)
    assert fresh.tenant_id is None
    assert fresh.metadata["tenant_id"] == TENANT
    assert fresh.lifecycle_state == "consultable"


def test_concurrent_owner_transition_is_preserved_as_409(boundary, monkeypatch):
    def race(_reader, _decision_id):
        current = boundary.owner._records.get(PERSONA)
        assert boundary.owner._records.compare_and_set(PERSONA, current, {**current, "lifecycle_state": "frozen"})[0]
        return evidence()
    monkeypatch.setattr(ApprovalReader, "get", race)
    with pytest.raises(_PersonaHttpResponseError) as failure:
        advance(boundary)
    assert failure.value.status_code == 409
    assert boundary.owner.get(PERSONA).lifecycle_state == "frozen"


def test_ordinary_create_route_forwards_authenticated_tenant_not_raw_metadata(boundary, monkeypatch):
    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.personas import PersonaService, create_personas_router
    from services.control_plane.bff.personas import service
    from services.control_plane.bff.personas.routes import collection
    from services.control_plane.bff.persona_provisioning_coordinator import PersonaProvisioningCoordinationError

    created_ids = []

    def create_owner_step(record, *, payload, owner):
        service._persona_record_for_provisioning(record, payload=payload, owner=owner, mutate_store=True)
        created_ids.append(record.persona_id)
        # This contract isolates the real Persona step, not Capital/Runtime onboarding.
        raise PersonaProvisioningCoordinationError("Other provisioning owners intentionally not mounted")

    monkeypatch.setattr(collection, "_coordinate_persona_create", create_owner_step)
    svc = PersonaService(read_store=boundary.port, write_owner=boundary.port,
                         ranking_write_owner=SimpleNamespace(), command_store=SimpleNamespace())
    app = FastAPI()
    app.include_router(create_personas_router(service=svc, extract_identity_fn=extract_identity_jwt))
    with TestClient(app) as client:
        response = client.post("/bff/personas", headers={
            "Authorization": bearer(), "Idempotency-Key": "new-authenticated-persona",
        }, json={"name": "New authenticated persona", "metadata": {"tenant_id": "foreign"}})
    assert response.status_code == 503, response.text
    assert len(created_ids) == 1
    fresh = PersistentPersonaOwner.from_json_path(boundary.path).get(created_ids[0])
    assert fresh.tenant_id == TENANT
    assert fresh.metadata["tenant_id"] == TENANT
    # No metadata fallback is needed for the owner to admit a same-tenant JWT.
    accepted = boundary.port.advance_lifecycle(created_ids[0], actor_id="alice",
        target_state="research_only", governance_decision_id=None,
        authorization=bearer(roles=("persona.admin",)))
    assert accepted["tenant_id"] == TENANT
    assert accepted["updated_by"] == "alice"


@pytest.mark.parametrize("response", [[], {}, {"persona_id": "foreign", "lifecycle_state": "paper_owner"}])
def test_invalid_owner_success_never_becomes_a_fabricated_readback(boundary, monkeypatch, response):
    monkeypatch.setattr(boundary.port, "_request", lambda *a, **k: response)
    with pytest.raises(PersonaWriteOwnerUnavailable, match="invalid readback"):
        advance(boundary)
