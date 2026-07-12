from __future__ import annotations

import os
import sys
import uuid
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import main as bff_main
from agora.strategy_workshop.router import _ws_replay_after

AUTH = {"Authorization": "Bearer interaction-user:operator", "Idempotency-Key": "idem-context-p3"}


class FakeReadStore:
    def list_personas(self, **kwargs):
        return [
            {"persona_id": "ready", "tenant_id": "pantheon-dev", "display_name": "Ready", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "draft", "tenant_id": "pantheon-dev", "display_name": "Draft", "lifecycle_state": "draft", "environment_ceiling": "paper"},
            {"persona_id": "research", "tenant_id": "pantheon-dev", "display_name": "Research", "lifecycle_state": "active", "environment_ceiling": "research"},
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["persona_opinion"]}


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


def test_durable_opinions_debate_and_synthesis_flow(monkeypatch):
    c = client(monkeypatch)
    
    # 1. Resolve context
    resolve_headers = {**AUTH, "Idempotency-Key": f"idem-context-{uuid.uuid4().hex[:8]}"}
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=resolve_headers, json=context_payload()).json()["data"]
    workshop_id = resolved["workshop_id"]

    # 2. Submit interaction (normal flow)
    payload = {
        "workshop_id": workshop_id,
        "mode": "consult",
        "environment": "paper",
        "topic": "Strategy debate and consensus search",
        "participant_persona_ids": ["ready"],
        "context_refs": context_payload()["context_refs"]
    }
    
    # We use a unique idempotency key
    headers = {**AUTH, "Idempotency-Key": f"idem-interaction-{uuid.uuid4().hex[:8]}"}
    response = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    
    # Verify events were created in the store
    events_response = c.get(f"/bff/agora/workshops/{workshop_id}/events", headers=AUTH)
    assert events_response.status_code == 200, events_response.text
    events = events_response.json()["data"]
    assert len(events) >= 3  # opinion_requested, opinion_offered, thread_closed
    
    # Check opinion_offered event
    offered_ev = next(e for e in events if e["event_type"] == "opinion_offered")
    assert offered_ev["actor_type"] == "persona_session"
    
    # Verify the full payload survived persistence/readback
    full_payload = offered_ev["payload_refs_json"]
    assert full_payload["spec_version"] == "1.0"
    assert "opinion" in full_payload
    assert full_payload["opinion"]["stance"] == "agree"
    assert full_payload["opinion"]["confidence"] == 0.90
    assert "ev-telemetry-001" in full_payload["opinion"]["evidence_refs"]

    # Check cards
    cards_response = c.get(f"/bff/agora/workshops/{workshop_id}/cards", headers=AUTH)
    assert cards_response.status_code == 200, cards_response.text
    cards = cards_response.json()["data"]
    
    # Verify consult_result card is present
    consult_card = next(card for card in cards if card["card_type"] == "consult_result")
    assert consult_card["status"] == "completed"
    assert consult_card["payload"]["status"] == "recommendation"
    assert "agree" in consult_card["summary"].lower() or "agree" in consult_card["title"].lower() or "synthesized" in consult_card["title"].lower()


def test_durable_opinions_no_consensus_flow(monkeypatch):
    c = client(monkeypatch)
    resolve_headers = {**AUTH, "Idempotency-Key": f"idem-context-{uuid.uuid4().hex[:8]}"}
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=resolve_headers, json=context_payload()).json()["data"]
    workshop_id = resolved["workshop_id"]

    payload = {
        "workshop_id": workshop_id,
        "mode": "consult",
        "environment": "paper",
        "topic": "Debate with no_consensus outcome",
        "participant_persona_ids": ["ready"],
        "context_refs": context_payload()["context_refs"]
    }
    
    headers = {**AUTH, "Idempotency-Key": f"idem-interaction-{uuid.uuid4().hex[:8]}"}
    response = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    
    # Verify consult_result card has no_consensus status
    cards = c.get(f"/bff/agora/workshops/{workshop_id}/cards", headers=AUTH).json()["data"]
    consult_card = next(card for card in cards if card["card_type"] == "consult_result")
    assert consult_card["payload"]["status"] == "no_consensus"
    assert len(consult_card["payload"]["disagreements"]) > 0


def test_durable_opinions_more_research_flow(monkeypatch):
    c = client(monkeypatch)
    resolve_headers = {**AUTH, "Idempotency-Key": f"idem-context-{uuid.uuid4().hex[:8]}"}
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=resolve_headers, json=context_payload()).json()["data"]
    workshop_id = resolved["workshop_id"]

    payload = {
        "workshop_id": workshop_id,
        "mode": "consult",
        "environment": "paper",
        "topic": "Debate triggering more_research",
        "participant_persona_ids": ["ready"],
        "context_refs": context_payload()["context_refs"]
    }
    
    headers = {**AUTH, "Idempotency-Key": f"idem-interaction-{uuid.uuid4().hex[:8]}"}
    response = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    
    # Verify consult_result card has more_research_required status and conditions
    cards = c.get(f"/bff/agora/workshops/{workshop_id}/cards", headers=AUTH).json()["data"]
    consult_card = next(card for card in cards if card["card_type"] == "consult_result")
    assert consult_card["payload"]["status"] == "more_research_required"
    assert len(consult_card["payload"]["conditions"]) > 0


def test_durable_opinions_homogeneity_warning_flow(monkeypatch):
    c = client(monkeypatch)
    resolve_headers = {**AUTH, "Idempotency-Key": f"idem-context-{uuid.uuid4().hex[:8]}"}
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=resolve_headers, json=context_payload()).json()["data"]
    workshop_id = resolved["workshop_id"]

    payload = {
        "workshop_id": workshop_id,
        "mode": "consult",
        "environment": "paper",
        "topic": "Debate checking for homogeneity and correlation",
        "participant_persona_ids": ["ready"],
        "context_refs": context_payload()["context_refs"]
    }
    
    headers = {**AUTH, "Idempotency-Key": f"idem-interaction-{uuid.uuid4().hex[:8]}"}
    response = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    
    # Verify consult_result card contains homogeneity warning in risk notes
    cards = c.get(f"/bff/agora/workshops/{workshop_id}/cards", headers=AUTH).json()["data"]
    consult_card = next(card for card in cards if card["card_type"] == "consult_result")
    risk_notes = consult_card["payload"]["risk_notes"]
    assert any("homogeneity" in note.lower() or "correlation" in note.lower() for note in risk_notes)


def test_durable_opinions_degraded_path_flow(monkeypatch):
    c = client(monkeypatch)
    resolve_headers = {**AUTH, "Idempotency-Key": f"idem-context-{uuid.uuid4().hex[:8]}"}
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=resolve_headers, json=context_payload()).json()["data"]
    workshop_id = resolved["workshop_id"]

    payload = {
        "workshop_id": workshop_id,
        "mode": "consult",
        "environment": "paper",
        "topic": "Debate under degraded provider conditions",
        "participant_persona_ids": ["ready"],
        "context_refs": context_payload()["context_refs"]
    }
    
    headers = {**AUTH, "Idempotency-Key": f"idem-interaction-{uuid.uuid4().hex[:8]}"}
    response = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    
    # Check that events contain openclaw.degraded or abstain stance
    events_response = c.get(f"/bff/agora/workshops/{workshop_id}/events", headers=AUTH)
    assert events_response.status_code == 200, events_response.text
    events = events_response.json()["data"]
    offered_ev = next(e for e in events if e["event_type"] == "opinion_offered")
    assert offered_ev["payload_refs_json"]["opinion"]["stance"] == "abstain"


def test_sse_replay_database_fallback(monkeypatch):
    from agora.router import make_workshop_store
    store = make_workshop_store()
    
    # 1. Create a dummy workshop and events directly in store
    workshop_id = f"ws-{uuid.uuid4().hex[:8]}"
    store.create_session({
        "workshop_id": workshop_id,
        "tenant_id": "pantheon-dev",
        "user_id": "test-user",
        "status": "open"
    })
    
    ev1_id = f"evt-1-{uuid.uuid4().hex[:8]}"
    store.create_event({
        "event_id": ev1_id,
        "workshop_id": workshop_id,
        "actor_type": "operator",
        "event_type": "opinion_requested",
        "payload_refs_json": {"topic": "test"},
        "trace_id": "trace-1"
    })
    
    ev2_id = f"evt-2-{uuid.uuid4().hex[:8]}"
    store.create_event({
        "event_id": ev2_id,
        "workshop_id": workshop_id,
        "actor_type": "persona_session",
        "event_type": "opinion_offered",
        "payload_refs_json": {"opinion": {"stance": "agree", "confidence": 0.9, "rationale": "ok"}},
        "trace_id": "trace-1"
    })

    # 2. Check replay from database using _ws_replay_after
    replayed = _ws_replay_after(workshop_id, ev1_id, store=store)
    assert len(replayed) == 1
    assert replayed[0]["id"] == ev2_id
