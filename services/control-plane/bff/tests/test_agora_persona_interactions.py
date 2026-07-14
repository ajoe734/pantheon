from __future__ import annotations

import os
import sys
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import main as bff_main
from agora.strategy_workshop.store import MemoryWorkshopStore

AUTH = {"Authorization": "Bearer interaction-user:operator", "Idempotency-Key": "idem-context-1"}


class FakeReadStore:
    def list_personas(self, **kwargs):
        return [
            {"persona_id": "ready", "tenant_id": "pantheon-dev", "display_name": "Ready", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "draft", "tenant_id": "pantheon-dev", "display_name": "Draft", "lifecycle_state": "draft", "environment_ceiling": "paper"},
            {"persona_id": "research", "tenant_id": "pantheon-dev", "display_name": "Research", "lifecycle_state": "active", "environment_ceiling": "research"},
            {"persona_id": "unscoped", "tenant_id": None, "display_name": "Unscoped", "lifecycle_state": "active", "environment_ceiling": "paper"},
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["persona_opinion"]}

    def list_approval_decisions(self):
        return []


def client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setattr(bff_main, "read_store", FakeReadStore())
    return TestClient(bff_main.app, raise_server_exceptions=False)


def context_payload(version="v1"):
    return {"environment": "paper", "context_refs": [
        {"type": "strategy", "id": "strategy-1", "version_id": version},
        {"type": "decision_event", "id": "decision-1"},
    ]}


def test_context_resolution_is_idempotent_and_versioned(monkeypatch):
    c = client(monkeypatch)
    first = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload())
    second = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload())
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["data"] == second.json()["data"]
    assert first.json()["data"]["verified"] is True
    invalid = c.post("/bff/agora/interactions/context:resolve", headers={**AUTH, "Idempotency-Key": "invalid"},
                     json={"context_refs": [{"type": "strategy", "id": "strategy-1"}]})
    assert invalid.status_code == 422


def test_eligibility_includes_and_excludes_with_reasons(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    response = c.post("/bff/agora/interactions/participants:eligible", headers={"Authorization": AUTH["Authorization"]}, json={
        "workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "required_capability": "persona_opinion",
    })
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [x["persona_id"] for x in body["included"]] == ["ready"]
    excluded = {x["persona_id"]: x["reasons"] for x in body["excluded"]}
    assert "persona_not_active" in excluded["draft"]
    assert "environment_ceiling_exceeded" in excluded["research"]
    assert "tenant_mismatch" in excluded["unscoped"]


def test_typed_submission_is_idempotent_and_has_no_write_authority(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    payload = {"workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "topic": "Review risk", "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    headers = {**AUTH, "Idempotency-Key": "interaction-1"}
    first = c.post("/bff/agora/interactions", headers=headers, json=payload)
    second = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202, first.text
    assert first.json()["data"] == second.json()["data"]
    assert first.json()["data"]["execution_authority"] == "none"
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    cards = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/cards", headers=AUTH).json()["data"]
    assert len(events) == 3
    assert len([card for card in cards if card["card_type"] == "consult_result"]) == 1


def test_submission_same_key_different_body_conflicts_without_duplicate_side_effects(monkeypatch):
    c = client(monkeypatch)
    key = f"interaction-fingerprint-{uuid.uuid4().hex}"
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"context-{key}"},
        json=context_payload(),
    ).json()["data"]
    body = {"workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "topic": "Review risk", "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": key}, json=body).status_code == 202
    changed = {**body, "topic": "Different request"}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": key}, json=changed).status_code == 409
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert [event["event_type"] for event in events] == ["opinion_requested", "opinion_offered", "thread_closed"]


def test_partial_side_effect_failure_replays_to_exactly_one_event_set(monkeypatch):
    c = client(monkeypatch)
    key = f"interaction-recovery-{uuid.uuid4().hex}"
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"context-{key}"},
        json=context_payload(),
    ).json()["data"]
    original = MemoryWorkshopStore.create_event
    calls = {"count": 0, "failed": False}

    def fail_after_first(self, event):
        calls["count"] += 1
        if calls["count"] == 2 and not calls["failed"]:
            calls["failed"] = True
            raise RuntimeError("simulated crash after first event")
        return original(self, event)

    monkeypatch.setattr(MemoryWorkshopStore, "create_event", fail_after_first)
    body = {"workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "topic": "Recover synthesis", "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    headers = {**AUTH, "Idempotency-Key": key}
    assert c.post("/bff/agora/interactions", headers=headers, json=body).status_code == 500
    assert c.post("/bff/agora/interactions", headers=headers, json=body).status_code == 202
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert [event["event_type"] for event in events] == ["opinion_requested", "opinion_offered", "thread_closed"]
    cards = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/cards", headers=AUTH).json()["data"]
    assert len([card for card in cards if card["card_type"] == "consult_result"]) == 1


def test_explicit_interaction_id_cannot_be_reused_for_different_content(monkeypatch):
    c = client(monkeypatch)
    suffix = uuid.uuid4().hex
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"context-collision-{suffix}"},
        json=context_payload(),
    ).json()["data"]
    body = {"interaction_id": f"explicit-{suffix}", "workshop_id": resolved["workshop_id"],
        "mode": "consult", "environment": "paper", "topic": "Original topic",
        "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": f"first-{suffix}"}, json=body).status_code == 202
    collision = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": f"second-{suffix}"},
        json={**body, "topic": "Conflicting topic"},
    )
    assert collision.status_code == 409
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert len(events) == 3


def test_propose_action_creates_canonical_governed_proposal_and_card(monkeypatch):
    c = client(monkeypatch)
    bff_main.read_store.list_approval_decisions = lambda: [
        {
            "decision_id": "approval-risk-valid",
            "state": "decided",
            "outcome": "approved",
            "target_type": "strategy_spec",
            "target_id": "strategy-1",
            "target_version": "v1",
            "tenant_id": "pantheon-dev",
            "owner_user_id": "interaction-user",
            "reviewer": "risk-reviewer",
            "actor_role": "risk_owner",
        },
        {
            "decision_id": "approval-self",
            "state": "decided",
            "outcome": "approved",
            "target_type": "strategy_spec",
            "target_id": "strategy-1",
            "target_version": "v1",
            "tenant_id": "pantheon-dev",
            "owner_user_id": "interaction-user",
            "reviewer": "interaction-user",
            "actor_role": "risk_owner",
        },
        {
            "decision_id": "approval-wrong-target",
            "state": "decided",
            "outcome": "approved",
            "target_type": "strategy_spec",
            "target_id": "strategy-other",
            "target_version": "v1",
            "tenant_id": "tenant-other",
            "owner_user_id": "interaction-user",
            "reviewer": "risk-reviewer",
            "actor_role": "risk_owner",
        },
    ]
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "proposal-context"},
        json=context_payload(),
    ).json()["data"]
    response = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "proposal-interaction"},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "propose_action",
            "environment": "paper",
            "topic": "Reduce risk budget to eight percent",
            "participant_persona_ids": ["ready"],
            "context_refs": context_payload()["context_refs"],
        },
    )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    proposal = data["proposal"]
    assert data["execution_authority"] == "none"
    assert data["proposal_ref"] == proposal["proposal_id"]
    assert proposal["target_id"] == "strategy-1"
    assert proposal["target_version"] == "v1"
    assert proposal["state"] == "draft"
    assert proposal["execution_authority"] == "none"
    assert proposal["available_approval_decision_refs"] == []
    assert proposal["approval_decision_readiness"]["reason"] == "proposal_not_validated"
    assert proposal["approval_decision_refs_authority"] == "canonical_read_store"

    readback = c.get(
        f"/bff/agora/proposals/{proposal['proposal_id']}",
        headers={"Authorization": AUTH["Authorization"]},
    )
    assert readback.status_code == 200, readback.text
    assert readback.headers["etag"] == data["proposal_etag"]
    assert readback.json()["data"]["available_approval_decision_refs"] == []

    cards = c.get(
        f"/bff/agora/workshops/{resolved['workshop_id']}/cards",
        headers=AUTH,
    ).json()["data"]
    governed = next(card for card in cards if card["card_type"] == "governed_proposal")
    assert governed["payload"]["proposal_id"] == proposal["proposal_id"]
    assert governed["payload"]["approval_refs"] == []
    assert not any(card["card_type"] == "consult_result" for card in cards)


def test_propose_action_requires_strategy_context_and_rejects_injected_refs(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "proposal-no-strategy-context"},
        json={
            "environment": "paper",
            "context_refs": [{"type": "decision_event", "id": "decision-1"}],
        },
    ).json()["data"]
    payload = {
        "workshop_id": resolved["workshop_id"],
        "mode": "propose_action",
        "environment": "paper",
        "topic": "Unbound candidate",
        "participant_persona_ids": ["ready"],
        "context_refs": [{"type": "decision_event", "id": "decision-1"}],
    }
    missing_strategy = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "proposal-no-strategy"},
        json=payload,
    )
    assert missing_strategy.status_code == 422
    assert "strategy context" in missing_strategy.text

    injected = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "proposal-injected-ref"},
        json={**payload, "approval_refs": ["payload-controlled"]},
    )
    assert injected.status_code == 422


def test_ineligible_participant_and_cross_tenant_fail_closed(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    payload = {"workshop_id": resolved["workshop_id"], "mode": "ask", "environment": "paper", "topic": "Explain",
        "participant_persona_ids": ["draft"], "context_refs": context_payload()["context_refs"]}
    assert c.post("/bff/agora/interactions", headers={**AUTH, "Idempotency-Key": "bad-persona"}, json=payload).status_code == 422
    monkeypatch.setenv("PANTHEON_BFF_ALLOWED_TENANTS", "pantheon-dev")
    response = c.post("/bff/agora/interactions/participants:eligible",
        headers={"Authorization": AUTH["Authorization"], "X-Tenant-Id": "other"},
        json={"workshop_id": resolved["workshop_id"], "mode": "ask"})
    assert response.status_code == 403
