"""Mounted-route regressions: persona target binding, owner-write requirement, tenant-scoped durable approval reads."""
import asyncio

import pytest

from services.control_plane.bff.tests.test_command_adapters_router import (
    _WrapperParityHarness, _WP_ROLES,
)
from services.control_plane.bff.command_adapters.service import process_command
from services.control_plane.bff.command_queue import CommandStore


@pytest.mark.parametrize("wrapped", [False, True])
def test_confirmation_target_cannot_be_replaced_by_persona_param(tmp_path, monkeypatch, wrapped):
    h = _WrapperParityHarness(tmp_path, monkeypatch, "AdvanceLifecycle", _WP_ROLES)
    actor = "review-actor"
    h.issue_token("review-confirm", actor)
    params = h.base_params()
    params.update(persona_id="different-persona", approval_decision_id="approval-wp")
    if wrapped:
        params["action_id"] = "AdvanceLifecycle"
    response = h.client.post(
        "/bff/v1/commands",
        headers={"Authorization": "Bearer " + h.jwt(actor), "Idempotency-Key": "review-command", "X-Confirm-Token": "review-confirm"},
        json={"command": "PersonaAction" if wrapped else "AdvanceLifecycle", "target": {"type": h.target_type, "id": h.target_id}, "params": params, "audit_context": {"reason": "isolated target-binding review"}},
    )
    store = CommandStore(h.command_path)
    rows = [r for r in store._get_all_commands() if r["type"] in {"PersonaAction", "AdvanceLifecycle"}]
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    print({"wrapped": wrapped, "confirmed_target": h.target_id, "http": response.status_code, "calls": h.calls, "result": store.get_command(rows[0]["command_id"]) if rows else None})
    assert not any("/different-persona/" in c["url"] for c in h.calls), "Executed on a persona that the confirmation/approval did not authorize"


class _OwnerTransport:
    """Adapt the isolated Persona owner ASGI app to the port's opener seam."""

    def __init__(self, client):
        self._client = client
        self.requests = []

    def __call__(self, request, timeout=None):
        import io
        import urllib.error
        from urllib.parse import urlsplit

        parts = urlsplit(request.full_url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        response = self._client.request(
            request.get_method(), target, content=request.data, headers=dict(request.header_items())
        )
        self.requests.append((request.get_method(), parts.path, response.status_code))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, "owner", {}, io.BytesIO(response.content))

        class _Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _Resp(response.content)


class _ApprovingVerifier:
    def verify_persona_lifecycle_decision(self, *, decision_id, persona_id, source_state, target_state):
        return decision_id == "approval-wp"


@pytest.mark.parametrize(
    "canonical,path,target",
    [
        ("PromoteCandidate", ["research_only", "consultable"], "paper_owner"),
        ("Demote", ["research_only", "consultable", "paper_owner"], "frozen"),
    ],
)
def test_persona_success_requires_owner_write(tmp_path, monkeypatch, canonical, path, target):
    """Real transition against the isolated Persona owner, with restart readback."""
    from fastapi.testclient import TestClient

    from services.control_plane.bff.command_adapters import persona_adapter
    from services.control_plane.bff.ports.persona_write_owner import PersonaRegistryHttpWritePort
    from services.persona.write_owner import PersistentPersonaOwner, create_app

    service_token, service_actor = "owner-service-token", "operator-bff"
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", service_token)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", service_actor)
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    personas_path = tmp_path / "personas.json"

    def owner_client():
        app = create_app(
            owner=PersistentPersonaOwner.from_json_path(personas_path),
            governance_decision_verifier=_ApprovingVerifier(),
        )
        return TestClient(app, raise_server_exceptions=False)

    h = _WrapperParityHarness(tmp_path, monkeypatch, canonical, _WP_ROLES)
    persona_id = h.target_id
    client = owner_client()
    auth = {"Authorization": f"Bearer {service_token}"}
    created = client.post(
        "/api/personas",
        headers=auth,
        json={"actor_id": service_actor, "persona_id": persona_id, "name": "P", "mandate": "isolated owner"},
    )
    assert created.status_code == 201, created.text
    for state in path:
        moved = client.patch(
            f"/api/personas/{persona_id}/lifecycle",
            headers=auth,
            json={"actor_id": service_actor, "target_state": state, "governance_decision_id": "approval-wp"},
        )
        assert moved.status_code == 200, moved.text
    source_state = path[-1]

    transport = _OwnerTransport(client)
    monkeypatch.setattr(
        persona_adapter,
        "_persona_owner",
        lambda: PersonaRegistryHttpWritePort(
            base_url="http://persona-owner.test", service_token=service_token,
            service_actor_id=service_actor, opener=transport,
        ),
    )
    actor = "review-actor"
    h.issue_token("review-confirm", actor)
    params = h.base_params()
    params.update(action_id=canonical, approval_decision_id="approval-wp", target_state=target)
    response = h.client.post(
        "/bff/v1/commands",
        headers={"Authorization": "Bearer " + h.jwt(actor), "Idempotency-Key": "review-command", "X-Confirm-Token": "review-confirm"},
        json={"command": "PersonaAction", "target": {"type": h.target_type, "id": h.target_id}, "params": params, "audit_context": {"reason": "isolated owner-write review"}},
    )
    assert response.status_code == 202, response.text
    store = CommandStore(h.command_path)
    rows = [r for r in store._get_all_commands() if r["type"] == "PersonaAction"]
    assert len(rows) == 1
    asyncio.run(process_command(rows[0]["command_id"], command_store=store))
    final = store.get_command(rows[0]["command_id"])
    assert final["status"] == "executed", final
    assert ("PATCH", f"/api/personas/{persona_id}/lifecycle", 200) in transport.requests
    receipt = final["result"]["domain_receipt"] if "domain_receipt" in final.get("result", {}) else final["result"]
    assert receipt["from_state"] == source_state and receipt["to_state"] == target
    assert owner_client().get(f"/api/personas/{persona_id}").json()["lifecycle_state"] == target
    # Restart parity: a fresh CommandStore still reports the same terminal row.
    restarted = CommandStore(h.command_path).get_command(rows[0]["command_id"])
    assert restarted["status"] == "executed"


@pytest.mark.parametrize("detail", [False, True])
def test_durable_approval_reads_respect_authenticated_tenant(tmp_path, monkeypatch, detail):
    import time
    from types import SimpleNamespace
    from services.control_plane.bff.auth.policy import extract_identity_jwt
    from services.control_plane.bff.governance.router import create_governance_router
    from services.control_plane.bff.governance.service import GovernanceService
    from services.runtime_auth_inbound import encode_jwt_hs256

    h = _WrapperParityHarness(tmp_path, monkeypatch, "AdvanceLifecycle", _WP_ROLES)
    h.client.app.include_router(create_governance_router(
        governance_service=GovernanceService(SimpleNamespace(dataset_source=lambda name: "canonical"), command_store=h.store),
        command_store=h.store, extract_identity=extract_identity_jwt,
    ))
    created = h.client.post(
        "/api/v1/approval-decisions",
        headers={"Authorization": "Bearer " + h.jwt("actor-a"), "Idempotency-Key": "tenant-a-approval"},
        json={"plan_id": "tenant-a-private-plan", "decision_id": "tenant-a-private-decision", "decision": "approve", "memo": "private tenant A rationale"},
    )
    assert created.status_code == 202, created.text
    now = int(time.time())
    token_b = encode_jwt_hs256({"sub": "actor-b", "roles": ["viewer"], "iss": h.aud, "aud": h.aud, "iat": now - 10, "exp": now + 300, "tenant_id": "tenant-b"}, secret=h.secret)
    path = "/api/v1/approval-decisions" + ("/tenant-a-private-decision" if detail else "")
    response = h.client.get(path, headers={"Authorization": "Bearer " + token_b})
    print({"detail": detail, "http": response.status_code, "body": response.json()})
    assert "tenant-a-private-plan" not in response.text, "Approval query leaked tenant A's durable record to authenticated tenant B"
