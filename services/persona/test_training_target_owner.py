"""Focused tests for the Persona training-target authority contract.

These exercise ``services/persona/write_owner.py``'s new training-target
endpoints against the frozen client validator in
``services/training-session/persona_target.py`` (loaded standalone, exactly as
that service's own tests load it) so the Persona owner is proven against the
exact contract the training-session authority boundary enforces, without
touching that frozen module or the Governance owner.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from services.persona.write_owner import (
    PersistentPersonaOwner,
    PersistentPersonaTrainingTargetOwner,
    create_app,
)

SERVICE_TOKEN = "training-target-owner-test-service-token"  # noqa: S105 - test fixture, not a real credential
SERVICE_ACTOR = "operator-bff"
TENANT_ID = "tenant-alpha"
OTHER_TENANT_ID = "tenant-beta"
PERSONA_ID = "persona-alpha"
TRAINING_TARGET_URL = f"http://persona.test/api/personas/{PERSONA_ID}/training-target"
APPROVAL_URL = "http://governance.test/api/governance/approvals/approval-alpha"


def _load_persona_target_module():
    """Load the frozen training-session client exactly as its own tests do."""

    name = "persona_target_owner_contract_test_module"
    sys.modules.pop(name, None)
    training_session_dir = Path(__file__).resolve().parents[1] / "training-session"
    spec = importlib.util.spec_from_file_location(
        name, training_session_dir / "persona_target.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _BridgeTransport:
    """Routes the frozen client's persona calls to a real owner TestClient.

    The approval readback is out of this task's scope (Governance owner); it
    is served from a fixed in-memory response so the commit path can still be
    proven end to end against the real Persona owner.
    """

    def __init__(self, client: TestClient, approval_response: Mapping[str, Any], module) -> None:
        self._client = client
        self._approval_response = dict(approval_response)
        self._module = module

    def request(self, method, url, *, headers, json_body, timeout_seconds):
        if url == APPROVAL_URL:
            return self._module.PersonaTargetResponse(200, {"approval": self._approval_response})
        assert url == TRAINING_TARGET_URL
        response = self._client.request(
            method, "/api/personas/persona-alpha/training-target",
            headers=dict(headers),
            json=json_body,
        )
        return self._module.PersonaTargetResponse(response.status_code, response.json())


@pytest.fixture()
def owner_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", SERVICE_ACTOR)
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    personas_path = tmp_path / "personas.json"
    targets_path = tmp_path / "training_targets.json"
    persona_owner = PersistentPersonaOwner.from_json_path(personas_path)
    training_target_owner = PersistentPersonaTrainingTargetOwner.from_json_path(
        targets_path, persona_owner=persona_owner
    )
    app = create_app(owner=persona_owner, training_target_owner=training_target_owner)
    client = TestClient(app, raise_server_exceptions=False)
    _create_persona(client)
    return client, personas_path, targets_path


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}


def _create_persona(client: TestClient) -> None:
    response = client.post(
        "/api/personas",
        json={
            "actor_id": SERVICE_ACTOR,
            "persona_id": PERSONA_ID,
            "name": "Alpha",
            "mandate": "focused training-authority test persona",
        },
        headers=_auth_header(),
    )
    assert response.status_code == 201, response.text


def _commit_inputs(module, precondition: Mapping[str, Any], *, session_id: str, trusted_now: datetime):
    generation = precondition["target_generation"]
    candidate = {"persona_patch_ref": f"patch-gen-{generation}", "parameters": {"risk.max_drawdown": 0.08}}
    control_state = {"risk.max_drawdown": 0.08, "risk.max_leverage": 1.25}
    candidate_digest = module.canonical_digest(candidate)
    control_digest = module.canonical_digest(control_state)
    approval_decision_ref = f"approval-decision://{PERSONA_ID}/{session_id}/generation-{generation}"
    proof = {
        "proof_ref": f"trainer-eval-proof:{session_id}:eval-{generation}",
        "status": "passed",
        "tenant_id": TENANT_ID,
        "candidate_binding": candidate,
        "candidate_digest": candidate_digest,
        "controls": control_state,
        "controls_digest": control_digest,
        "authority": {"policy": {"approval_decision_ref": approval_decision_ref}},
        "target_precondition": {
            "persona_id": PERSONA_ID,
            "tenant_id": TENANT_ID,
            "expected_previous_generation": precondition["expected_previous_generation"],
            "target_generation": generation,
            "precondition_digest": precondition["precondition_digest"],
            "controller_record_ref": precondition["controller_record_ref"],
        },
    }
    proof_digest = module.canonical_digest(proof)
    proof["proof_digest"] = proof_digest
    decided_at = trusted_now - timedelta(minutes=1)
    expires_at = trusted_now + timedelta(hours=1)
    approval = {
        "decision_id": f"approval-{PERSONA_ID}-{generation}",
        "approval_decision_ref": approval_decision_ref,
        "decision_state": "decided",
        "decision": "approved",
        "persona_id": PERSONA_ID,
        "tenant_id": TENANT_ID,
        "session_id": session_id,
        "candidate_digest": candidate_digest,
        "proof_digest": proof_digest,
        "decided_at": decided_at.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "authority_status": "authoritative",
        "controller_record_ref": f"governance-controller://approval-{PERSONA_ID}-{generation}",
        "recorded_at": decided_at.isoformat().replace("+00:00", "Z"),
    }
    inputs = {
        "persona_id": PERSONA_ID,
        "tenant_id": TENANT_ID,
        "session_id": session_id,
        "candidate": candidate,
        "control_state": control_state,
        "evaluation_proof": proof,
        "generation": generation,
        "idempotency_key": f"teach-{session_id}-generation-{generation}",
        "expected_precondition_digest": precondition["precondition_digest"],
        "approval_decision_ref": approval_decision_ref,
        "persona_readback_url": TRAINING_TARGET_URL,
        "approval_readback_url": APPROVAL_URL,
        "target_write_url": TRAINING_TARGET_URL,
        "target_readback_url": TRAINING_TARGET_URL,
        "authorization_token": SERVICE_TOKEN,
        "trusted_now": trusted_now,
    }
    return inputs, approval


def test_precondition_read_commit_and_restart_readback(owner_env) -> None:
    client, personas_path, targets_path = owner_env
    module = _load_persona_target_module()
    trusted_now = datetime.now(timezone.utc)

    precondition = module.read_persona_target_precondition(
        persona_id=PERSONA_ID,
        tenant_id=TENANT_ID,
        persona_readback_url=TRAINING_TARGET_URL,
        authorization_token=SERVICE_TOKEN,
        trusted_now=trusted_now,
        transport=_BridgeTransport(client, {}, module),
    )
    assert precondition["status"] == "active"
    assert precondition["current_generation"] == 0
    assert precondition["target_generation"] == 1

    inputs, approval = _commit_inputs(module, precondition, session_id="session-alpha", trusted_now=trusted_now)
    transport = _BridgeTransport(client, approval, module)

    receipt = module.commit_persona_target(**inputs, transport=transport)

    assert receipt["status"] == "committed"
    assert receipt["generation"] == 1
    assert receipt["pre_generation"] == 0
    assert receipt["replayed"] is False
    committed_ref = receipt["target_controller_record_ref"]

    # Simulate a fresh owner process reading the same durable store back.
    restarted_persona_owner = PersistentPersonaOwner.from_json_path(personas_path)
    restarted_training_target_owner = PersistentPersonaTrainingTargetOwner.from_json_path(
        targets_path, persona_owner=restarted_persona_owner
    )
    restarted_app = create_app(
        owner=restarted_persona_owner, training_target_owner=restarted_training_target_owner
    )
    restarted_client = TestClient(restarted_app, raise_server_exceptions=False)
    terminal = restarted_client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": TENANT_ID},
    )
    assert terminal.status_code == 200, terminal.text
    body = terminal.json()
    assert body["status"] == "committed"
    assert body["generation"] == 1
    assert body["controller_record_ref"] == committed_ref
    assert body["candidate_digest"] == receipt["candidate_digest"]
    assert body["control_digest"] == receipt["control_digest"]
    assert body["proof_digest"] == receipt["proof_digest"]
    assert body["approval_digest"] == receipt["approval_digest"]


def _first_commit(client: TestClient, module, *, session_id: str = "session-alpha"):
    trusted_now = datetime.now(timezone.utc)
    precondition = module.read_persona_target_precondition(
        persona_id=PERSONA_ID,
        tenant_id=TENANT_ID,
        persona_readback_url=TRAINING_TARGET_URL,
        authorization_token=SERVICE_TOKEN,
        trusted_now=trusted_now,
        transport=_BridgeTransport(client, {}, module),
    )
    inputs, approval = _commit_inputs(module, precondition, session_id=session_id, trusted_now=trusted_now)
    receipt = module.commit_persona_target(
        **inputs, transport=_BridgeTransport(client, approval, module)
    )
    assert receipt["status"] == "committed"
    return receipt, inputs


def test_cross_tenant_read_and_write_are_denied(owner_env) -> None:
    client, _personas_path, _targets_path = owner_env
    module = _load_persona_target_module()
    _first_commit(client, module)

    read = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": OTHER_TENANT_ID},
    )
    assert read.status_code == 403
    assert read.json()["detail"]

    write = client.post(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": OTHER_TENANT_ID, "Idempotency-Key": "cross-tenant-attempt"},
        json={
            "persona_id": PERSONA_ID,
            "tenant_id": OTHER_TENANT_ID,
            "session_id": "session-hostile",
            "candidate_digest": "a" * 64,
            "control_digest": "b" * 64,
            "proof_digest": "c" * 64,
            "approval_digest": "d" * 64,
            "generation": 1,
            "expected_previous_generation": 0,
            "expected_precondition_digest": "e" * 64,
            "expected_precondition_record_ref": "forged-ref",
            "approval_decision_id": "forged-approval",
            "approval_decision_ref": "forged-approval-ref",
        },
    )
    assert write.status_code == 403


def test_semantic_idempotency_conflict_rejects_reused_generation_new_payload(owner_env) -> None:
    client, _personas_path, _targets_path = owner_env
    module = _load_persona_target_module()
    receipt, inputs = _first_commit(client, module)

    conflicting = client.post(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": TENANT_ID, "Idempotency-Key": "different-session-same-generation"},
        json={
            "persona_id": PERSONA_ID,
            "tenant_id": TENANT_ID,
            "session_id": "session-different",
            "candidate_digest": receipt["candidate_digest"],
            "control_digest": receipt["control_digest"],
            "proof_digest": receipt["proof_digest"],
            "approval_digest": receipt["approval_digest"],
            "generation": 1,
            "expected_previous_generation": 0,
            "expected_precondition_digest": inputs["expected_precondition_digest"],
            "expected_precondition_record_ref": "some-ref",
            "approval_decision_id": "approval-id",
            "approval_decision_ref": "approval-ref",
        },
    )
    assert conflicting.status_code == 409


def test_stale_generation_commit_is_rejected(owner_env) -> None:
    client, _personas_path, _targets_path = owner_env
    module = _load_persona_target_module()
    _first_commit(client, module, session_id="session-one")
    _first_commit(client, module, session_id="session-two")  # advances generation 1 -> 2

    stale = client.post(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": TENANT_ID, "Idempotency-Key": "stale-attempt"},
        json={
            "persona_id": PERSONA_ID,
            "tenant_id": TENANT_ID,
            "session_id": "session-stale",
            "candidate_digest": "a" * 64,
            "control_digest": "b" * 64,
            "proof_digest": "c" * 64,
            "approval_digest": "d" * 64,
            "generation": 1,
            "expected_previous_generation": 0,
            "expected_precondition_digest": "e" * 64,
            "expected_precondition_record_ref": "stale-ref",
            "approval_decision_id": "stale-approval",
            "approval_decision_ref": "stale-approval-ref",
        },
    )
    assert stale.status_code == 409


def test_replayed_commit_at_same_generation_and_binding_is_idempotent(owner_env) -> None:
    client, _personas_path, _targets_path = owner_env
    module = _load_persona_target_module()
    trusted_now = datetime.now(timezone.utc)
    precondition = module.read_persona_target_precondition(
        persona_id=PERSONA_ID,
        tenant_id=TENANT_ID,
        persona_readback_url=TRAINING_TARGET_URL,
        authorization_token=SERVICE_TOKEN,
        trusted_now=trusted_now,
        transport=_BridgeTransport(client, {}, module),
    )
    inputs, approval = _commit_inputs(module, precondition, session_id="session-alpha", trusted_now=trusted_now)
    first = module.commit_persona_target(**inputs, transport=_BridgeTransport(client, approval, module))
    assert first["replayed"] is False

    second = module.commit_persona_target(**inputs, transport=_BridgeTransport(client, approval, module))
    assert second["replayed"] is True
    assert second["target_controller_record_ref"] == first["target_controller_record_ref"]
    assert second["generation"] == first["generation"]
