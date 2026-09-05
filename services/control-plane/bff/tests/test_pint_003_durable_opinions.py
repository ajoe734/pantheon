from __future__ import annotations

import os
import json
import sys
import uuid
import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff import main as bff_main
from services.control_plane.bff.agora.interaction import runner as interaction_runner
from services.control_plane.bff.agora.interaction.worker import AgoraInteractionWorker
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClientError
from services.control_plane.bff.agora.strategy_workshop.router import _ws_replay_after

AUTH = {"Authorization": "Bearer interaction-user:operator", "Idempotency-Key": "idem-context-p3"}


def _run_worker(provider=None):
    worker = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
        client_factory=(lambda: provider) if provider else None,
    )
    return worker.run_once(limit=10)


class FakeReadStore:
    def list_personas(self, **kwargs):
        return [
            {"persona_id": "ready", "tenant_id": "pantheon-dev", "display_name": "Ready", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "risk", "tenant_id": "pantheon-dev", "display_name": "Risk", "lifecycle_state": "active", "environment_ceiling": "paper"},
            {"persona_id": "draft", "tenant_id": "pantheon-dev", "display_name": "Draft", "lifecycle_state": "draft", "environment_ceiling": "paper"},
            {"persona_id": "research", "tenant_id": "pantheon-dev", "display_name": "Research", "lifecycle_state": "active", "environment_ceiling": "research"},
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        return {"snapshot_id": f"snap-{persona_id}", "capabilities": ["persona_opinion"]}


class FakeProvider:
    def __init__(self, conclusions=None, *, unavailable=False, fail_personas=None):
        self.conclusions = conclusions or {"ready": "support", "risk": "oppose"}
        self.unavailable = unavailable
        self.fail_personas = set(fail_personas or [])
        self.calls = []

    def ensure_persona_opinion_agent(self, admission, *, persona_profile):
        return {"agent": {"agent_id": admission["agent_id"]}, "execution_authority": "none"}

    def invoke_assistant_provider(self, **kwargs):
        persona_id = kwargs["context_pack"]["participant"]["persona_id"]
        self.calls.append(persona_id)
        if self.unavailable or persona_id in self.fail_personas:
            raise OpenClawOpsClientError(
                "provider unavailable", status_code=503, error_code="OPENCLAW_UNAVAILABLE"
            )
        conclusion = self.conclusions[persona_id]
        text = json.dumps({
            "conclusion": conclusion,
            "rationale": f"provider rationale for {persona_id}",
            "confidence": 0.9,
            "uncertainty": ["sample uncertainty"] if conclusion == "insufficient_evidence" else [],
            "risks": ["correlation risk"],
            "invalidation_conditions": ["collect more evidence"],
            "evidence_refs": [],
            "recommended_measures": [],
        })
        return {"status": "ok", "data": {"status": "completed", "output": {
            "agent_id": kwargs["agent_id"],
            "request_id": f"response-{persona_id}",
            "json_events": [{"type": "item.completed", "item": {"text": text}}],
        }}}


def test_partial_retry_only_calls_latest_retryable_persona_and_reuses_persisted_opinion(monkeypatch):
    provider = FakeProvider(fail_personas={"risk"})
    c = client(monkeypatch, provider)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"partial-context-{uuid.uuid4().hex}"},
        json=context_payload(),
    ).json()["data"]
    submitted = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": f"partial-submit-{uuid.uuid4().hex}"},
        json={
            "workshop_id": resolved["workshop_id"],
            "mode": "consult",
            "environment": "paper",
            "topic": "Compare independent Persona conclusions",
            "participant_persona_ids": ["ready", "risk"],
            "context_refs": context_payload()["context_refs"],
        },
    )
    assert submitted.status_code == 202, submitted.text
    interaction_id = submitted.json()["data"]["interaction_id"]
    _run_worker(provider)
    first = c.get(f"/bff/agora/interactions/{interaction_id}", headers=AUTH).json()["data"]
    assert first["status"] == "degraded"
    assert provider.calls == ["ready", "risk"]
    ready_opinion_id = first["opinions"][0]["opinion_id"]

    provider.fail_personas.clear()
    retried = c.post(
        f"/bff/agora/interactions/{first['interaction_id']}:retry",
        headers={**AUTH, "Idempotency-Key": f"partial-retry-{uuid.uuid4().hex}"},
        json={"reason": "Risk Persona provider recovered"},
    )
    assert retried.status_code == 202, retried.text
    _run_worker(provider)
    result = c.get(f"/bff/agora/interactions/{interaction_id}", headers=AUTH).json()["data"]
    assert result["status"] == "completed"
    assert provider.calls == ["ready", "risk", "risk"]
    assert len([item for item in result["provider_invocations"]
                if item["participant"]["persona_id"] == "ready"]) == 1
    assert len([item for item in result["provider_invocations"]
                if item["participant"]["persona_id"] == "risk"]) == 2
    assert ready_opinion_id in result["synthesis"]["opinion_ids"]
    assert set(result["synthesis"]["opinion_ids"]) == {
        item["opinion_id"] for item in result["opinions"]
    }


@pytest.mark.parametrize("degraded_reason", [
    "OPENCLAW_UNAVAILABLE",
    "OPENCLAW_GATEWAY_TIMEOUT",
    "OPENCLAW_GATEWAY_INVOCATION_FAILED",
])
def test_adapter_shaped_transient_degraded_reason_remains_retryable(monkeypatch, degraded_reason):
    class AdapterDegradedProvider(FakeProvider):
        degraded = True

        def invoke_assistant_provider(self, **kwargs):
            if self.degraded:
                persona_id = kwargs["context_pack"]["participant"]["persona_id"]
                self.calls.append(persona_id)
                return {
                    "status": "ok",
                    "data": {
                        "provider": "openclaw",
                        "status": "degraded",
                        "output": {
                            "json_events": [],
                            "reason": degraded_reason,
                            "message": "gateway temporarily unavailable",
                        },
                    },
                }
            return super().invoke_assistant_provider(**kwargs)

    provider = AdapterDegradedProvider()
    c = client(monkeypatch, provider)
    resolved = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"degraded-context-{uuid.uuid4().hex}"},
        json=context_payload(),
    ).json()["data"]
    submitted = c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": f"degraded-submit-{uuid.uuid4().hex}"},
        json={
            "workshop_id": resolved["workshop_id"], "mode": "consult",
            "environment": "paper", "topic": "Retry transient adapter outage",
            "participant_persona_ids": ["ready"],
            "context_refs": context_payload()["context_refs"],
        },
    )
    assert submitted.status_code == 202, submitted.text
    interaction_id = submitted.json()["data"]["interaction_id"]
    _run_worker(provider)
    first = c.get(f"/bff/agora/interactions/{interaction_id}", headers=AUTH).json()["data"]
    assert first["status"] == "failed"
    assert first["provider_invocations"][0]["error"] == {
        "code": degraded_reason,
        "message": "gateway temporarily unavailable",
        "retryable": True,
    }

    provider.degraded = False
    retried = c.post(
        f"/bff/agora/interactions/{first['interaction_id']}:retry",
        headers={**AUTH, "Idempotency-Key": f"degraded-retry-{uuid.uuid4().hex}"},
        json={"reason": "OpenClaw gateway recovered"},
    )
    assert retried.status_code == 202, retried.text
    _run_worker(provider)
    retried_detail = c.get(f"/bff/agora/interactions/{interaction_id}", headers=AUTH).json()["data"]
    assert retried_detail["status"] == "completed"
    assert provider.calls == ["ready", "ready"]


def client(monkeypatch, provider=None):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setattr(bff_main, "read_store", FakeReadStore())
    monkeypatch.setattr(interaction_runner, "OpenClawOpsClient", lambda: provider or FakeProvider())
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
    _run_worker()

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
    assert full_payload["spec_version"] == "1.9"
    assert "opinion" in full_payload
    assert full_payload["opinion"]["conclusion"] == "support"
    assert full_payload["opinion"]["confidence"] == 0.90
    assert full_payload["opinion"]["provenance"]["simulation"] is False

    # Check cards
    cards_response = c.get(f"/bff/agora/workshops/{workshop_id}/cards", headers=AUTH)
    assert cards_response.status_code == 200, cards_response.text
    cards = cards_response.json()["data"]

    # Verify consult_result card is present
    consult_card = next(card for card in cards if card["card_type"] == "consult_result")
    assert consult_card["status"] == "completed"
    assert consult_card["payload"]["status"] == "recommendation"
    assert "provider rationale for ready" in consult_card["summary"]


def test_durable_opinions_no_consensus_flow(monkeypatch):
    provider = FakeProvider({"ready": "support", "risk": "oppose"})
    c = client(monkeypatch, provider)
    resolve_headers = {**AUTH, "Idempotency-Key": f"idem-context-{uuid.uuid4().hex[:8]}"}
    resolved = c.post("/bff/agora/interactions/context:resolve", headers=resolve_headers, json=context_payload()).json()["data"]
    workshop_id = resolved["workshop_id"]

    payload = {
        "workshop_id": workshop_id,
        "mode": "consult",
        "environment": "paper",
        "topic": "Debate with no_consensus outcome",
        "participant_persona_ids": ["ready", "risk"],
        "context_refs": context_payload()["context_refs"]
    }

    headers = {**AUTH, "Idempotency-Key": f"idem-interaction-{uuid.uuid4().hex[:8]}"}
    response = c.post("/bff/agora/interactions", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    _run_worker(provider)

    # Verify consult_result card has no_consensus status
    cards = c.get(f"/bff/agora/workshops/{workshop_id}/cards", headers=AUTH).json()["data"]
    consult_card = next(card for card in cards if card["card_type"] == "consult_result")
    assert consult_card["payload"]["status"] == "no_consensus"
    assert len(consult_card["payload"]["synthesis"]["disagreements"]) > 0


def test_durable_opinions_more_research_flow(monkeypatch):
    provider = FakeProvider({"ready": "insufficient_evidence"})
    c = client(monkeypatch, provider)
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
    _run_worker(provider)

    # Verify consult_result card has more_research_required status and conditions
    cards = c.get(f"/bff/agora/workshops/{workshop_id}/cards", headers=AUTH).json()["data"]
    consult_card = next(card for card in cards if card["card_type"] == "consult_result")
    assert consult_card["payload"]["status"] == "more_research_required"
    assert len(consult_card["payload"]["synthesis"]["conditions"]) > 0


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
    _run_worker()

    # Verify consult_result card contains homogeneity warning in risk notes
    cards = c.get(f"/bff/agora/workshops/{workshop_id}/cards", headers=AUTH).json()["data"]
    consult_card = next(card for card in cards if card["card_type"] == "consult_result")
    risk_notes = consult_card["payload"]["synthesis"]["risk_notes"]
    assert any("correlation" in note.lower() for note in risk_notes)


def test_durable_opinions_degraded_path_flow(monkeypatch):
    provider = FakeProvider(unavailable=True)
    c = client(monkeypatch, provider)
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
    _run_worker(provider)

    # Provider failure is persisted without manufacturing an abstain opinion.
    events_response = c.get(f"/bff/agora/workshops/{workshop_id}/events", headers=AUTH)
    assert events_response.status_code == 200, events_response.text
    events = events_response.json()["data"]
    failed_ev = next(e for e in events if e["event_type"] == "provider_invocation_failed")
    assert failed_ev["payload_refs_json"]["opinion"] is None


def test_sse_replay_database_fallback(monkeypatch):
    from services.control_plane.bff.agora.router import make_workshop_store
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
