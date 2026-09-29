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


@pytest.mark.parametrize("canonical", ["PromoteCandidate", "Demote"])
def test_persona_success_requires_owner_write(tmp_path, monkeypatch, canonical):
    h = _WrapperParityHarness(tmp_path, monkeypatch, canonical, _WP_ROLES)
    actor = "review-actor"
    h.issue_token("review-confirm", actor)
    params = h.base_params()
    params.update(action_id=canonical, approval_decision_id="approval-wp")
    response = h.client.post(
        "/bff/v1/commands",
        headers={"Authorization": "Bearer " + h.jwt(actor), "Idempotency-Key": "review-command", "X-Confirm-Token": "review-confirm"},
        json={"command": "PersonaAction", "target": {"type": h.target_type, "id": h.target_id}, "params": params, "audit_context": {"reason": "isolated owner-write review"}},
    )
    store = CommandStore(h.command_path)
    rows = [r for r in store._get_all_commands() if r["type"] == "PersonaAction"]
    for row in rows:
        asyncio.run(process_command(row["command_id"], command_store=store))
    final = store.get_command(rows[0]["command_id"]) if rows else {}
    print({"canonical": canonical, "http": response.status_code, "calls": h.calls, "final": final})
    assert final.get("status") != "executed" or h.calls, "Reports executed and authoritative persona state without invoking its owner"


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
