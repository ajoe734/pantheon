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
        "expected_tenant_id": TENANT,
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
        target_state="research_only", governance_decision_id=None, expected_tenant_id=TENANT,
        authorization=bearer(roles=("persona.admin",)))
    assert accepted["tenant_id"] == TENANT
    assert accepted["updated_by"] == "alice"


@pytest.mark.parametrize("response", [[], {}, {"persona_id": "foreign", "lifecycle_state": "paper_owner"}, {
    "persona_id": PERSONA, "lifecycle_state": "paper_owner", "updated_by": "alice", "tenant_id": "foreign",
    "metadata": {"last_lifecycle_governance_decision_id": "decision-paper"},
}])
def test_invalid_owner_success_never_becomes_a_fabricated_readback(boundary, monkeypatch, response):
    monkeypatch.setattr(boundary.port, "_request", lambda *a, **k: response)
    with pytest.raises(PersonaWriteOwnerUnavailable, match="invalid readback"):
        advance(boundary)


@pytest.mark.parametrize("action", ["AdvanceLifecycle", "advance_lifecycle", "promote", "PromoteCandidate", "Demote"])
@pytest.mark.parametrize("audit_context", ["flat-reason", None, [], {}, False])
def test_rest_lifecycle_delegates_to_shared_admission(boundary, action, audit_context):
    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.personas import PersonaService, create_personas_router
    calls = []

    def admission(**kwargs):
        calls.append(kwargs)
        return {"forwarded": True}

    persona = PersonaService(read_store=boundary.port, write_owner=boundary.port,
                             ranking_write_owner=SimpleNamespace(), command_store=SimpleNamespace())
    app = FastAPI()
    app.state.command_adapter_service = SimpleNamespace(submit_command_admission=admission)
    app.include_router(create_personas_router(service=persona, extract_identity_fn=extract_identity_jwt))
    body = {"target_state": "paper_owner", "governance_decision_id": "decision-paper",
            "reason": "Isolated delegation contract"}
    if audit_context != "flat-reason":
        body["audit_context"] = audit_context
    with TestClient(app) as client:
        response = client.post(f"/bff/personas/{PERSONA}/actions/{action}", headers={
            "Authorization": bearer(), "Idempotency-Key": "rest-lifecycle",
            "X-Confirm-Token": "actual-confirmation", "X-Trace-Id": "trace-isolated",
        }, json=body)
    assert response.status_code == 202, response.text
    assert len(calls) == 1
    forwarded = calls[0]
    assert forwarded["authorization"] == bearer()
    assert forwarded["x_confirm_token"] == "actual-confirmation"
    assert forwarded["x_trace_id"] == "trace-isolated"
    assert forwarded["idempotency_key"] == "rest-lifecycle"
    assert forwarded["payload"]["command"] == "PersonaAction"
    assert forwarded["payload"]["action"] == action
    assert forwarded["payload"]["target"] == {"type": "Persona", "id": PERSONA}
    assert forwarded["payload"]["params"]["governance_decision_id"] == "decision-paper"
    expected_audit = {"reason": body["reason"]} if audit_context == "flat-reason" else audit_context
    assert forwarded["payload"]["audit_context"] == expected_audit
    assert boundary.calls == []  # this tests delegation, not accepted owner execution


@pytest.fixture
def mounted(boundary, monkeypatch):
    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.command_adapters import CommandAdapterService, create_command_adapters_router
    from services.control_plane.bff.command_adapters import persona_adapter
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.personas import PersonaService, create_personas_router

    monkeypatch.setattr(persona_adapter, "create_persona_registry_write_owner", lambda: boundary.port)
    store = CommandStore(str(boundary.tmp_path / "commands.jsonl"))
    svc = CommandAdapterService(command_store=store, read_surface=boundary.port,
                                extract_identity=extract_identity_jwt)
    persona = PersonaService(read_store=boundary.port, write_owner=boundary.port,
                             ranking_write_owner=SimpleNamespace(), command_store=store)
    app = FastAPI()
    app.state.command_adapter_service = svc
    app.include_router(create_command_adapters_router(service=svc))
    app.include_router(create_personas_router(service=persona, extract_identity_fn=extract_identity_jwt))
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, store=store, boundary=boundary, svc=svc)


def token(mounted, *, command="AdvanceLifecycle", persona_id=PERSONA, authorization=None):
    response = mounted.client.post("/bff/confirm-tokens", headers={
        "Authorization": authorization or bearer(), "Idempotency-Key": "issue-" + command + persona_id,
    }, json={"command": command, "target": {"type": "Persona", "id": persona_id}})
    assert response.status_code == 201, response.text
    return response.json()["data"]["tokenId"]


ENTRY_POINTS = (
    "AdvanceLifecycle", "PromoteCandidate", "Demote", "wrapper/advance_lifecycle",
    "wrapper/promote", "wrapper/demote", "rest/AdvanceLifecycle", "rest/promote", "rest/Demote",
)


def submit(mounted, entry, *, confirmation, authorization=None, params=None, key="lifecycle-operation"):
    values = {"persona_id": PERSONA, "target_state": "paper_owner",
              "governance_decision_id": "decision-paper", "actor_id": "spoofed-client", **(params or {})}
    headers = {"Authorization": authorization or bearer(), "Idempotency-Key": key}
    if confirmation:
        headers["X-Confirm-Token"] = confirmation
    if entry.startswith("rest/"):
        return mounted.client.post(f"/bff/personas/{PERSONA}/actions/{entry.split('/')[1]}",
                                   headers=headers, json={**values, "reason": "Isolated lifecycle"})
    payload = {"command": entry, "target": {"type": "Persona", "id": PERSONA},
               "params": values, "audit_context": {"reason": "Isolated lifecycle"}}
    if entry.startswith("wrapper/"):
        payload.update(command="PersonaAction", action=entry.split("/")[1])
    return mounted.client.post("/bff/v1/commands", headers=headers, json=payload)


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_mounted_lifecycle_entries_use_one_real_owner(mounted, entry):
    confirmation = token(mounted, command=entry if entry in ENTRY_POINTS[:3] else "AdvanceLifecycle")
    response = submit(mounted, entry, confirmation=confirmation)
    assert response.status_code == 202, response.text
    command = mounted.store.get_command(response.json()["data"]["command_id"])
    assert command["type"] == "AdvanceLifecycle"
    assert command["status"] == "executed", command.get("error")
    fresh = PersistentPersonaOwner.from_json_path(mounted.boundary.path).get(PERSONA)
    assert fresh.lifecycle_state == "paper_owner"
    assert fresh.updated_by == "alice"
    assert command["result"]["authoritative_readback"] == fresh.model_dump(mode="json")
    assert len(mounted.boundary.calls) == 1
    assert mounted.boundary.calls[0]["headers"]["Authorization"] == bearer()
    assert mounted.boundary.calls[0]["body"]["actor_id"] == "alice"
    assert bearer().removeprefix("Bearer ") not in (mounted.boundary.tmp_path / "commands.jsonl").read_text()
    repeated = submit(mounted, entry, confirmation=confirmation)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["data"]["command_id"] == command["command_id"]
    assert len(mounted.boundary.calls) == 1


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.parametrize("wrong_token", [False, True])
def test_mounted_entries_share_real_confirmation_gate(mounted, entry, wrong_token):
    confirmation = token(mounted, command="ApprovePool") if wrong_token else None
    response = submit(mounted, entry, confirmation=confirmation)
    assert response.status_code == 428, response.text
    assert mounted.boundary.calls == []
    assert all(r["type"] == "CreateConfirmToken" for r in mounted.store._get_all_commands())


@pytest.mark.parametrize("entry", ["AdvanceLifecycle", "wrapper/promote", "rest/promote"])
@pytest.mark.parametrize("field", ["approvalId", "approval_decision_id"])
def test_mounted_existing_decision_references_are_forwarded_not_trusted(mounted, entry, field):
    response = submit(mounted, entry, confirmation=token(mounted),
                      params={"governance_decision_id": None, field: "decision-paper"})
    assert response.status_code == 202, response.text
    record = mounted.store.get_command(response.json()["data"]["command_id"])
    assert record["status"] == "executed", record.get("error")
    assert mounted.boundary.calls[0]["body"]["governance_decision_id"] == "decision-paper"


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_mounted_conflicting_decision_references_are_rejected(mounted, entry):
    response = submit(mounted, entry, confirmation=token(mounted), params={"approvalId": "different"})
    assert response.status_code == 422, response.text
    assert mounted.boundary.calls == []


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_mounted_entries_reject_unverified_approval_at_owner(mounted, entry):
    response = submit(mounted, entry, confirmation=token(mounted), params={"governance_decision_id": "invented"})
    assert response.status_code == 202, response.text
    record = mounted.store.get_command(response.json()["data"]["command_id"])
    assert record["status"] == "failed"
    assert record["error"]["downstream_status"] == 403
    assert not record.get("result")
    assert mounted.boundary.owner.get(PERSONA).lifecycle_state == "consultable"


@pytest.mark.parametrize("entry", ["Observe", "wrapper/observe", "rest/Observe"])
def test_mounted_observe_is_retired_not_a_fake_write(mounted, entry):
    response = submit(mounted, entry, confirmation=None)
    assert response.status_code == 410, response.text
    assert mounted.boundary.calls == []
    assert mounted.store._get_all_commands() == []


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_mounted_cross_tenant_retry_cannot_replay_owner_receipt(mounted, entry, monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", TENANT + ",foreign")
    confirmation = token(mounted)
    response = submit(mounted, entry, confirmation=confirmation)
    assert response.status_code == 202, response.text
    foreign = submit(mounted, entry, confirmation=confirmation, authorization=bearer(tenant="foreign"))
    assert foreign.status_code == 409, foreign.text
    assert len(mounted.boundary.calls) == 1
    command_id = response.json()["data"]["command_id"]
    own = mounted.client.get(f"/api/v1/operator/commands/{command_id}", headers={"Authorization": bearer()})
    assert own.status_code == 200, own.text
    hidden = mounted.client.get(f"/api/v1/operator/commands/{command_id}",
                                headers={"Authorization": bearer(tenant="foreign")})
    assert hidden.status_code == 404, hidden.text


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_mounted_target_conflicts_never_write_another_persona(mounted, entry):
    response = submit(mounted, entry, confirmation=token(mounted), params={"persona_id": "different-persona"})
    assert response.status_code == 422, response.text
    assert mounted.boundary.calls == []


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_mounted_missing_target_is_not_guessed(mounted, entry):
    response = submit(mounted, entry, confirmation=token(mounted), params={"target_state": ""})
    assert response.status_code == 422, response.text
    assert mounted.boundary.calls == []


@pytest.mark.parametrize("entry", ["Demote", "wrapper/demote", "rest/Demote"])
def test_mounted_demote_uses_explicit_governed_state_not_fabricated_demoted(mounted, entry):
    mounted.boundary.records["decision-paper"] = evidence(metadata={
        **evidence().metadata,
        "subject": {"persona_id": PERSONA, "from_state": "consultable", "to_state": "frozen"},
    })
    response = submit(mounted, entry, confirmation=token(mounted), params={"target_state": "frozen"})
    assert response.status_code == 202, response.text
    record = mounted.store.get_command(response.json()["data"]["command_id"])
    assert record["status"] == "executed", record.get("error")
    assert mounted.boundary.owner.get(PERSONA).lifecycle_state == "frozen"
    assert record["result"]["authoritative_readback"]["lifecycle_state"] == "frozen"


@pytest.mark.parametrize("entry", ENTRY_POINTS)
@pytest.mark.parametrize("failure,status", [("unavailable", 503), ("race", 409), ("unbound", 403), ("missing", 404), ("invalid_state", 422)])
def test_mounted_owner_failures_remain_distinct(mounted, entry, failure, status, monkeypatch):
    if failure == "unavailable":
        mounted.boundary.records["decision-paper"] = ConnectionError("isolated governance unavailable")
    elif failure == "race":
        def race(_reader, _decision_id):
            current = mounted.boundary.owner._records.get(PERSONA)
            assert mounted.boundary.owner._records.compare_and_set(PERSONA, current, {**current, "lifecycle_state": "frozen"})[0]
            return evidence()
        monkeypatch.setattr(ApprovalReader, "get", race)
    elif failure in {"missing", "unbound"}:
        current = mounted.boundary.owner._records.get(PERSONA)
        if failure == "missing":
            assert mounted.boundary.owner._records.delete_if_matches(PERSONA, current)
        else:
            assert mounted.boundary.owner._records.compare_and_set(PERSONA, current, {**current, "tenant_id": None})[0]
    params = {"target_state": "invalid_state"} if failure == "invalid_state" else None
    response = submit(mounted, entry, confirmation=token(mounted), params=params)
    assert response.status_code == 202, response.text
    record = mounted.store.get_command(response.json()["data"]["command_id"])
    assert record["status"] == "failed"
    assert record["error"]["downstream_status"] == status, record.get("error")
    assert not record.get("result")
    current = mounted.boundary.owner._records.get(PERSONA)
    assert current is None or current["lifecycle_state"] != "paper_owner"


@pytest.mark.parametrize("entry", ENTRY_POINTS)
def test_mounted_lifecycle_requires_authenticated_caller(mounted, entry):
    payload = {"command": entry, "target": {"type": "Persona", "id": PERSONA},
               "params": {"target_state": "paper_owner"}, "audit_context": {"reason": "unauthenticated"}}
    url = "/bff/v1/commands"
    if entry.startswith("wrapper/"):
        payload.update(command="PersonaAction", action=entry.split("/")[1])
    elif entry.startswith("rest/"):
        url = f"/bff/personas/{PERSONA}/actions/{entry.split('/')[1]}"
        payload = {"target_state": "paper_owner", "reason": "unauthenticated"}
    response = mounted.client.post(url, json=payload, headers={"Idempotency-Key": "unauthenticated"})
    assert response.status_code == 401, response.text
    assert mounted.boundary.calls == []
    assert mounted.store._get_all_commands() == []
