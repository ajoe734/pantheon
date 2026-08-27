from __future__ import annotations

import os
import sys
import uuid

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import main as bff_main
from agora.interaction.worker import AgoraInteractionWorker
from agora.strategy_workshop.store import MemoryWorkshopStore

AUTH = {"Authorization": "Bearer interaction-user:operator", "Idempotency-Key": "idem-context-1"}


class FakeReadStore:
    def list_personas(self, **kwargs):
        return [
            {"persona_id": "ready", "tenant_id": "pantheon-dev", "display_name": "Ready", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "paper-running", "tenant_id": "pantheon-dev", "display_name": "Paper Running", "lifecycle_state": "paper_running", "metadata": {"deployment_stage": "paper", "environment_ceiling": "paper"}},
            {"persona_id": "draft", "tenant_id": "pantheon-dev", "display_name": "Draft", "lifecycle_state": "draft", "environment_ceiling": "paper"},
            {"persona_id": "research", "tenant_id": "pantheon-dev", "display_name": "Research", "lifecycle_state": "active", "environment_ceiling": "research"},
            {"persona_id": "unscoped", "tenant_id": None, "display_name": "Unscoped", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "snapshot-missing", "tenant_id": "pantheon-dev", "display_name": "No Snapshot", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "capability-missing", "tenant_id": "pantheon-dev", "display_name": "No Capability", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "pointer-mismatch", "tenant_id": "pantheon-dev", "display_name": "Mismatched Snapshot", "lifecycle_state": "active", "environment_ceiling": "paper", "metadata": {"capability_snapshot_id": "snap-wrong-persona"}},
            {"persona_id": "deployed", "tenant_id": "pantheon-dev", "display_name": "Generic Deployed", "lifecycle_state": "deployed", "metadata": {"deployment_stage": "paper"}},
            {"persona_id": "stage-only", "tenant_id": "pantheon-dev", "display_name": "Stage Only", "lifecycle_state": "active", "metadata": {"deployment_stage": "paper"}},
            {"persona_id": "ceiling-unknown", "tenant_id": "pantheon-dev", "display_name": "Unknown Ceiling", "lifecycle_state": "active"},
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        if persona_id == "snapshot-missing":
            return None
        if persona_id == "capability-missing":
            return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["research_only"]}
        return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["persona_opinion"]}

    def get_persona(self, persona_id):
        return next(
            (row for row in self.list_personas() if row.get("persona_id") == persona_id),
            None,
        )

    def get_strategy_spec_detail(self, strategy_id, *, version_selector=None):
        if strategy_id != "strategy-1" or version_selector != "v1":
            return None
        return {
            "strategy_id": strategy_id,
            "strategy_spec_registry_id": version_selector,
        }

    def get_capability_snapshot(self, snapshot_id):
        if snapshot_id == "snap-wrong-persona":
            return {
                "snapshot_id": snapshot_id,
                "persona_id": "another-persona",
                "capabilities": ["persona_opinion"],
            }
        return None

    def list_approval_decisions(self):
        return []

    def list_decision_journal_entries(self):
        return [{"id": "entry-7", "tenant_id": "pantheon-dev", "owner_user_id": "interaction-user"}]


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
    workshop_id = first.json()["data"]["workshop_id"]
    workshop = c.get(f"/bff/agora/workshops/{workshop_id}", headers=AUTH)
    assert workshop.status_code == 200, workshop.text
    session = workshop.json()["data"]
    assert session["strategy_id"] == "strategy-1"
    assert session["active_strategy_spec_registry_id"] == "v1"
    assert session["selected_version_id"] is None
    invalid = c.post("/bff/agora/interactions/context:resolve", headers={**AUTH, "Idempotency-Key": "invalid"},
                     json={"context_refs": [{"type": "strategy", "id": "strategy-1"}]})
    assert invalid.status_code == 422


def test_context_resolution_rejects_strategy_or_registry_version_mismatch(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "pointer-authority"},
        json=context_payload(),
    ).json()["data"]

    for key, strategy_id, version_id in (
        ("wrong-strategy", "strategy-other", "v1"),
        ("wrong-version", "strategy-1", "v2"),
    ):
        response = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**AUTH, "Idempotency-Key": key},
            json={
                "workshop_id": resolved["workshop_id"],
                "environment": "paper",
                "context_refs": [
                    {"type": "strategy", "id": strategy_id, "version_id": version_id},
                ],
            },
        )
        assert response.status_code == 409, response.text
        assert "strategy_version_mismatch" in response.text


def test_submission_uses_strategy_registry_pointer_not_workshop_version_alias(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "registry-pointer-submit"},
        json=context_payload(),
    ).json()["data"]
    original_get_session = MemoryWorkshopStore.get_session

    def get_session_with_unrelated_workshop_version(self, workshop_id):
        session = original_get_session(self, workshop_id)
        if session is not None and workshop_id == resolved["workshop_id"]:
            session["selected_version_id"] = "workshop-version-unrelated"
        return session

    monkeypatch.setattr(MemoryWorkshopStore, "get_session", get_session_with_unrelated_workshop_version)

    response = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": "registry-pointer-interaction"},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "consult",
            "environment": "paper",
            "topic": "Review immutable strategy registry pointer",
            "participant_persona_ids": ["ready"],
            "context_refs": context_payload()["context_refs"],
        },
    )

    assert response.status_code == 202, response.text


def test_eligibility_includes_and_excludes_with_reasons(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=AUTH, json=context_payload()).json()["data"]
    response = c.post("/bff/agora/interactions/participants:eligible", headers={"Authorization": AUTH["Authorization"]}, json={
        "workshop_id": resolved["workshop_id"], "mode": "consult", "environment": "paper",
        "required_capability": "persona_opinion",
    })
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [x["persona_id"] for x in body["included"]] == ["ready", "paper-running"]
    excluded = {x["persona_id"]: x["reasons"] for x in body["excluded"]}
    assert "persona_not_active" in excluded["draft"]
    assert "environment_ceiling_exceeded" in excluded["research"]
    assert "tenant_mismatch" in excluded["unscoped"]
    assert "capability_snapshot_unavailable" in excluded["snapshot-missing"]
    assert "required_capability_missing" in excluded["capability-missing"]
    assert "capability_snapshot_persona_mismatch" in excluded["pointer-mismatch"]
    assert "persona_not_active" in excluded["deployed"]
    assert "environment_ceiling_exceeded" in excluded["stage-only"]
    assert "environment_ceiling_exceeded" in excluded["ceiling-unknown"]


def test_unknown_environment_ceiling_fails_closed_for_research(monkeypatch):
    c = client(monkeypatch)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": "unknown-ceiling-context"},
        json={**context_payload(), "environment": "research"},
    ).json()["data"]
    response = c.post(
        "/bff/agora/interactions/participants:eligible",
        headers={"Authorization": AUTH["Authorization"]},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "consult",
            "environment": "research",
            "required_capability": "persona_opinion",
        },
    )
    assert response.status_code == 200, response.text
    excluded = {x["persona_id"]: x["reasons"] for x in response.json()["data"]["excluded"]}
    assert "environment_ceiling_exceeded" in excluded["ceiling-unknown"]


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
    AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
    ).run_once()
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
    AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
    ).run_once()
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert [event["event_type"] for event in events] == [
        "opinion_requested", "provider_invocation_failed", "thread_closed"
    ]


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
    assert c.post("/bff/agora/interactions", headers=headers, json=body).status_code == 202
    worker = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
    )
    worker.run_once()
    assert c.post("/bff/agora/interactions:recover", headers=AUTH).status_code == 202
    worker.run_once()
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert [event["event_type"] for event in events] == [
        "opinion_requested", "provider_invocation_failed", "thread_closed"
    ]
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
    AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
    ).run_once()
    events = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/events", headers=AUTH).json()["data"]
    assert len(events) == 3


def test_propose_action_collision_does_not_create_a_dangling_second_proposal(monkeypatch):
    c = client(monkeypatch)
    suffix = uuid.uuid4().hex
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"context-proposal-collision-{suffix}"},
        json=context_payload(),
    ).json()["data"]
    body = {"interaction_id": f"proposal-explicit-{suffix}", "workshop_id": resolved["workshop_id"],
        "mode": "propose_action", "environment": "paper", "topic": "Original measure",
        "participant_persona_ids": ["ready"], "context_refs": context_payload()["context_refs"]}
    first = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": f"proposal-first-{suffix}"},
        json=body,
    )
    assert first.status_code == 202, first.text
    proposal_id = first.json()["data"]["proposal_id"]
    collision = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": f"proposal-second-{suffix}"},
        json={**body, "topic": "Conflicting measure"},
    )
    assert collision.status_code == 409
    revisions = c.get(
        f"/bff/agora/proposals/{proposal_id}/revisions",
        headers={"Authorization": AUTH["Authorization"]},
    )
    assert revisions.status_code == 200
    assert len(revisions.json()["data"]) == 1
    AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
    ).run_once()
    cards = c.get(f"/bff/agora/workshops/{resolved['workshop_id']}/cards", headers=AUTH).json()["data"]
    assert len([card for card in cards if card["card_type"] == "governed_proposal"]) == 1


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

    AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
    ).run_once()

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
