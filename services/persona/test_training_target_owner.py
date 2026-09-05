"""Focused tests for the Persona training-target authority contract.

These exercise ``services/persona/write_owner.py``'s new training-target
endpoints against the frozen client validator in
``services/training-session/persona_target.py`` (loaded standalone, exactly as
that service's own tests load it) so the Persona owner is proven against the
exact contract the training-session authority boundary enforces, without
touching that frozen module or the Governance owner.

A dedicated block of tests below sends direct owner requests that bypass the
frozen client validator entirely, proving the owner itself -- not just the
happy-path client -- rejects an unissued approval, a wrong precondition, a
failed proof, a first-reader tenant hijack, and a replay whose real content
was swapped out from under its claimed digests, and that a genuine commit
applies a real, observable mutation to the Persona owner record.
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
    PatchPersonaRequest,
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


class _FakeGovernanceApprovalVerifier:
    """Owner-side test double for the real Governance approval boundary.

    Mirrors the checks the production ``HttpGovernanceApprovalVerifier``
    performs against Governance's own approval-readback response (decision
    state, exact persona/tenant/session/digest binding, expiry), but reads
    from an in-memory registry of approvals a test has proven were really
    issued instead of making a live HTTP call. An unregistered
    ``approval_decision_id`` -- an unissued approval -- always fails closed.
    """

    def __init__(self) -> None:
        self._issued: dict[str, dict[str, Any]] = {}

    def issue(self, approval: Mapping[str, Any]) -> None:
        decision_id = str(approval.get("decision_id") or approval.get("approval_id") or "").strip()
        assert decision_id, "test approval fixture must carry a decision_id"
        self._issued[decision_id] = dict(approval)

    def verify_training_target_approval(
        self,
        *,
        approval_decision_id: str,
        approval_decision_ref: str,
        persona_id: str,
        tenant_id: str,
        session_id: str,
        candidate_digest: str,
        proof_digest: str,
    ) -> bool:
        decision = self._issued.get(approval_decision_id)
        if decision is None:
            return False
        lifecycle = str(decision.get("decision_state") or "").strip().lower()
        outcome = str(decision.get("decision") or "").strip().lower()
        if lifecycle not in {"decided", "approved"}:
            return False
        if outcome and outcome != "approved":
            return False
        if decision.get("persona_id") != persona_id:
            return False
        if decision.get("tenant_id") != tenant_id:
            return False
        if decision.get("session_id") != session_id:
            return False
        if decision.get("candidate_digest") != candidate_digest:
            return False
        if decision.get("proof_digest") != proof_digest:
            return False
        if decision.get("approval_decision_ref") != approval_decision_ref:
            return False
        expires_at = decision.get("expires_at")
        if not isinstance(expires_at, str) or not expires_at.strip():
            return False
        normalized = expires_at[:-1] + "+00:00" if expires_at.endswith("Z") else expires_at
        if datetime.fromisoformat(normalized).astimezone(timezone.utc) <= datetime.now(timezone.utc):
            return False
        return True


@pytest.fixture()
def owner_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", SERVICE_ACTOR)
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    personas_path = tmp_path / "personas.json"
    targets_path = tmp_path / "training_targets.json"
    persona_owner = PersistentPersonaOwner.from_json_path(personas_path)
    verifier = _FakeGovernanceApprovalVerifier()
    training_target_owner = PersistentPersonaTrainingTargetOwner.from_json_path(
        targets_path, persona_owner=persona_owner, approval_verifier=verifier
    )
    app = create_app(owner=persona_owner, training_target_owner=training_target_owner)
    client = TestClient(app, raise_server_exceptions=False)
    _create_persona(client)
    return client, personas_path, targets_path, verifier


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
            # The Persona's real, governed ``owner`` field is the only
            # genuine tenant binding this data model has; the training-target
            # owner derives its tenant authority from it, never from a
            # caller-asserted ``X-Tenant-Id`` header.
            "owner": TENANT_ID,
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
    client, personas_path, targets_path, verifier = owner_env
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
    verifier.issue(approval)
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
        targets_path, persona_owner=restarted_persona_owner, approval_verifier=verifier
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

    # The commit is a real, applied mutation of the actual Persona owner
    # record -- not a second receipt-only store the owner never observes --
    # and it carries the actual candidate/control_state content, not just
    # digest metadata.
    persona = restarted_client.get(f"/api/personas/{PERSONA_ID}")
    assert persona.status_code == 200, persona.text
    metadata = persona.json()["metadata"]
    assert metadata["training_target_generation"] == 1
    assert metadata["training_target_controller_record_ref"] == committed_ref
    assert metadata["training_target_control_digest"] == receipt["control_digest"]
    assert metadata["training_target_candidate"] == inputs["candidate"]
    assert metadata["training_target_control_state"] == inputs["control_state"]


def test_commit_recovers_after_crash_between_durability_and_application(
    owner_env,
) -> None:
    """A crash between the target CAS and the Persona apply must not lie.

    Root review finding: the target store used to CAS ``status=committed``
    before the separate Persona patch ran, so a crash (or patch failure)
    between those two steps left a false terminal readback that a replay
    would return as success without ever repairing the real Persona record.
    Simulate that crash by committing directly against the training-target
    owner with the Persona apply monkeypatched to fail once, then prove a
    plain replay of the identical binding repairs and finalizes it.
    """

    client, _personas_path, targets_path, verifier = owner_env
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
    inputs, approval = _commit_inputs(
        module, precondition, session_id="session-crash", trusted_now=trusted_now
    )
    verifier.issue(approval)
    write_body = _write_body_from_client_inputs(module, inputs)

    from services.persona.write_owner import PersonaConcurrentUpdate

    real_apply = PersistentPersonaTrainingTargetOwner._apply_to_persona_owner
    calls = {"count": 0}

    def _failing_once(self, persona_id, request, committed):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PersonaConcurrentUpdate("simulated crash before Persona apply")
        return real_apply(self, persona_id, request, committed)

    PersistentPersonaTrainingTargetOwner._apply_to_persona_owner = _failing_once
    try:
        response = client.post(
            f"/api/personas/{PERSONA_ID}/training-target",
            headers={
                **_auth_header(),
                "X-Tenant-Id": TENANT_ID,
                "Idempotency-Key": "crash-recovery-attempt",
            },
            json=write_body,
        )
        assert response.status_code == 409, response.text
    finally:
        PersistentPersonaTrainingTargetOwner._apply_to_persona_owner = real_apply

    # The record is durably ``applying``, not a false terminal ``committed``:
    # a fresh read must fail closed rather than report success.
    mid_crash_read = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": TENANT_ID},
    )
    assert mid_crash_read.status_code == 200, mid_crash_read.text
    assert mid_crash_read.json()["status"] == "applying"

    persona_mid_crash = client.get(f"/api/personas/{PERSONA_ID}")
    assert persona_mid_crash.json()["metadata"].get("training_target_generation") != 1

    # Replaying the identical binding recovers: applies and finalizes.
    recovered = client.post(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={
            **_auth_header(),
            "X-Tenant-Id": TENANT_ID,
            "Idempotency-Key": "crash-recovery-attempt",
        },
        json=write_body,
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "committed"
    assert recovered.json()["replayed"] is True

    final_read = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": TENANT_ID},
    )
    assert final_read.json()["status"] == "committed"
    persona_after = client.get(f"/api/personas/{PERSONA_ID}")
    assert persona_after.json()["metadata"]["training_target_generation"] == 1


def test_apply_ignores_out_of_order_lower_generation(owner_env) -> None:
    """A stale generation's apply must not clobber a newer one.

    Root review finding: a later concurrent generation could apply out of
    order. Prove the owner's apply step is generation-gated: applying an
    older generation's content after a newer generation is already recorded
    on the Persona is a safe no-op, not a regression.
    """

    client, _personas_path, _targets_path, verifier = owner_env
    module = _load_persona_target_module()
    first_receipt, first_inputs = _first_commit(
        client, module, verifier, session_id="session-one"
    )
    second_receipt, _second_inputs = _first_commit(
        client, module, verifier, session_id="session-two"
    )
    assert second_receipt["generation"] == 2

    from services.persona.write_owner import (
        CommitPersonaTrainingTargetRequest,
        PersistentPersonaTrainingTargetOwner,
    )

    # Reconstruct the owner instance the app uses so we can call the internal
    # apply step directly with stale (generation-1) content.
    stale_request = CommitPersonaTrainingTargetRequest(
        persona_id=PERSONA_ID,
        tenant_id=TENANT_ID,
        session_id="session-one",
        candidate_digest=first_receipt["candidate_digest"],
        control_digest=first_receipt["control_digest"],
        proof_digest=first_receipt["proof_digest"],
        approval_digest=first_receipt["approval_digest"],
        generation=1,
        expected_previous_generation=0,
        expected_precondition_digest=first_receipt["expected_precondition_digest"],
        expected_precondition_record_ref=first_receipt[
            "expected_precondition_record_ref"
        ],
        approval_decision_id=first_receipt["approval_decision_id"],
        approval_decision_ref=first_receipt["approval_decision_ref"],
        candidate=first_inputs["candidate"],
        control_state=first_inputs["control_state"],
        evaluation_proof=first_inputs["evaluation_proof"],
    )

    from services.persona.write_owner import build_persona_training_target_owner

    persona_before = client.get(f"/api/personas/{PERSONA_ID}").json()
    assert persona_before["metadata"]["training_target_generation"] == 2

    training_target_owner = build_persona_training_target_owner(
        PersistentPersonaOwner.from_json_path(_personas_path),
        approval_verifier=verifier,
    )
    training_target_owner._apply_to_persona_owner(
        PERSONA_ID, stale_request, {"controller_record_ref": "stale-ref"}
    )

    persona_after = client.get(f"/api/personas/{PERSONA_ID}").json()
    assert persona_after["metadata"]["training_target_generation"] == 2
    assert (
        persona_after["metadata"]["training_target_candidate_digest"]
        == second_receipt["candidate_digest"]
    )


def _first_commit(client: TestClient, module, verifier, *, session_id: str = "session-alpha"):
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
    verifier.issue(approval)
    receipt = module.commit_persona_target(
        **inputs, transport=_BridgeTransport(client, approval, module)
    )
    assert receipt["status"] == "committed"
    return receipt, inputs


def test_cross_tenant_read_and_write_are_denied(owner_env) -> None:
    client, _personas_path, _targets_path, verifier = owner_env
    module = _load_persona_target_module()
    _first_commit(client, module, verifier)

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
            "candidate": {"forged": True},
            "control_state": {"forged": True},
            "evaluation_proof": {"status": "passed"},
        },
    )
    assert write.status_code == 403


def test_first_reader_cannot_hijack_tenant_via_read(owner_env) -> None:
    """A GET (or commit) from any non-owner tenant must be rejected outright.

    Root review finding: an earlier fix returned an unpersisted "virtual"
    generation-0 view with ``authority_status=authoritative`` to whichever
    tenant asked first, which still let a caller obtain an
    authoritative-looking precondition for a tenant it does not own. The
    training-target owner must derive tenant authority from the Persona's
    real, governed ``owner`` field and reject any other asserted tenant
    before ever returning a precondition -- not merely avoid persisting a
    fabricated read.
    """

    client, _personas_path, _targets_path, verifier = owner_env
    module = _load_persona_target_module()

    hostile_read = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": "tenant-hostile"},
    )
    assert hostile_read.status_code == 403

    another_read = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": "tenant-also-hostile"},
    )
    assert another_read.status_code == 403

    # The real, governed owner tenant can still read and commit normally.
    genuine_read = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": TENANT_ID},
    )
    assert genuine_read.status_code == 200
    assert genuine_read.json()["generation"] == 0

    receipt, _inputs = _first_commit(client, module, verifier)
    assert receipt["status"] == "committed"
    assert receipt["generation"] == 1

    still_locked_out = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": "tenant-hostile"},
    )
    assert still_locked_out.status_code == 403


def test_commit_rejects_unissued_approval(owner_env) -> None:
    client, _personas_path, _targets_path, verifier = owner_env
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
    # Deliberately never verifier.issue(approval): this approval was never
    # actually decided by Governance.
    write_body = _write_body_from_client_inputs(module, inputs)

    response = client.post(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={
            **_auth_header(),
            "X-Tenant-Id": TENANT_ID,
            "Idempotency-Key": "unissued-approval-attempt",
        },
        json=write_body,
    )
    assert response.status_code in (403, 422), response.text
    assert response.json()["detail"]

    # And the owner is proven never to have applied it.
    still_absent = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": TENANT_ID},
    )
    assert still_absent.json()["generation"] == 0


def test_commit_rejects_wrong_precondition_digest(owner_env) -> None:
    client, _personas_path, _targets_path, verifier = owner_env
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
    verifier.issue(approval)
    write_body = _write_body_from_client_inputs(module, inputs)
    write_body["expected_precondition_digest"] = "f" * 64  # wrong precondition

    response = client.post(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={
            **_auth_header(),
            "X-Tenant-Id": TENANT_ID,
            "Idempotency-Key": "wrong-precondition-attempt",
        },
        json=write_body,
    )
    assert response.status_code == 422, response.text


def test_commit_rejects_failed_evaluation_proof(owner_env) -> None:
    client, _personas_path, _targets_path, verifier = owner_env
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
    inputs["evaluation_proof"]["status"] = "failed"
    inputs["evaluation_proof"]["proof_digest"] = module.canonical_digest(
        {k: v for k, v in inputs["evaluation_proof"].items() if k != "proof_digest"}
    )
    verifier.issue(approval)
    write_body = _write_body_from_client_inputs(module, inputs)

    response = client.post(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={
            **_auth_header(),
            "X-Tenant-Id": TENANT_ID,
            "Idempotency-Key": "failed-proof-attempt",
        },
        json=write_body,
    )
    assert response.status_code == 422, response.text


def _write_body_from_client_inputs(module, inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Build the exact write body the frozen client would POST, directly.

    Lets a test bypass ``commit_persona_target`` (and thus its own
    pre-checks) while still exercising the real owner endpoint with a
    genuinely shaped body, proving the owner enforces these checks itself.
    """

    candidate_digest = module.canonical_digest(inputs["candidate"])
    control_digest = module.canonical_digest(inputs["control_state"])
    proof_digest = inputs["evaluation_proof"]["proof_digest"]
    approval_digest = module.canonical_digest({})
    return {
        "persona_id": inputs["persona_id"],
        "tenant_id": inputs["tenant_id"],
        "session_id": inputs["session_id"],
        "candidate_digest": candidate_digest,
        "control_digest": control_digest,
        "proof_digest": proof_digest,
        "approval_digest": approval_digest,
        "generation": inputs["generation"],
        "expected_previous_generation": inputs["generation"] - 1,
        "expected_precondition_digest": inputs["expected_precondition_digest"],
        "expected_precondition_record_ref": inputs["evaluation_proof"]["target_precondition"][
            "controller_record_ref"
        ],
        "approval_decision_id": f"approval-{PERSONA_ID}-{inputs['generation']}",
        "approval_decision_ref": inputs["approval_decision_ref"],
        "candidate": inputs["candidate"],
        "control_state": inputs["control_state"],
        "evaluation_proof": inputs["evaluation_proof"],
    }


def test_semantic_idempotency_conflict_rejects_reused_generation_new_payload(owner_env) -> None:
    client, _personas_path, _targets_path, verifier = owner_env
    module = _load_persona_target_module()
    receipt, inputs = _first_commit(client, module, verifier)

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
            "candidate": inputs["candidate"],
            "control_state": inputs["control_state"],
            "evaluation_proof": inputs["evaluation_proof"],
        },
    )
    assert conflicting.status_code in (409, 422), conflicting.text


def test_replay_with_swapped_content_same_claimed_digests_is_rejected(owner_env) -> None:
    """Root review finding: a replay must not trust caller-claimed digests.

    A second request changing the real candidate/proof content while
    keeping the *claimed* digest fields identical, under a different
    Idempotency-Key, must not be treated as an idempotent replay just
    because the (stale) claimed digests still match the stored record.
    """

    client, _personas_path, _targets_path, verifier = owner_env
    module = _load_persona_target_module()
    receipt, inputs = _first_commit(client, module, verifier)

    swapped_candidate = {**inputs["candidate"], "parameters": {"risk.max_drawdown": 0.99}}
    tampered = client.post(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={
            **_auth_header(),
            "X-Tenant-Id": TENANT_ID,
            "Idempotency-Key": "a-completely-different-idempotency-key",
        },
        json={
            "persona_id": PERSONA_ID,
            "tenant_id": TENANT_ID,
            "session_id": inputs["session_id"],
            "candidate_digest": receipt["candidate_digest"],  # stale claimed digest
            "control_digest": receipt["control_digest"],
            "proof_digest": receipt["proof_digest"],
            "approval_digest": receipt["approval_digest"],
            "generation": 1,
            "expected_previous_generation": 0,
            "expected_precondition_digest": inputs["expected_precondition_digest"],
            "expected_precondition_record_ref": inputs["evaluation_proof"]["target_precondition"][
                "controller_record_ref"
            ],
            "approval_decision_id": f"approval-{PERSONA_ID}-1",
            "approval_decision_ref": inputs["approval_decision_ref"],
            "candidate": swapped_candidate,  # real content changed
            "control_state": inputs["control_state"],
            "evaluation_proof": inputs["evaluation_proof"],
        },
    )
    assert tampered.status_code in (409, 422), tampered.text
    assert tampered.json().get("replayed") is not True

    # The genuinely committed target is unchanged.
    unchanged = client.get(
        f"/api/personas/{PERSONA_ID}/training-target",
        headers={**_auth_header(), "X-Tenant-Id": TENANT_ID},
    )
    assert unchanged.json()["candidate_digest"] == receipt["candidate_digest"]


def test_stale_generation_commit_is_rejected(owner_env) -> None:
    client, _personas_path, _targets_path, verifier = owner_env
    module = _load_persona_target_module()
    _first_commit(client, module, verifier, session_id="session-one")
    _first_commit(client, module, verifier, session_id="session-two")  # advances generation 1 -> 2

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
            "candidate": {"stale": True},
            "control_state": {"stale": True},
            "evaluation_proof": {"status": "passed"},
        },
    )
    assert stale.status_code == 409


def test_replayed_commit_at_same_generation_and_binding_is_idempotent(owner_env) -> None:
    client, _personas_path, _targets_path, verifier = owner_env
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
    verifier.issue(approval)
    first = module.commit_persona_target(**inputs, transport=_BridgeTransport(client, approval, module))
    assert first["replayed"] is False

    second = module.commit_persona_target(**inputs, transport=_BridgeTransport(client, approval, module))
    assert second["replayed"] is True
    assert second["target_controller_record_ref"] == first["target_controller_record_ref"]
    assert second["generation"] == first["generation"]
