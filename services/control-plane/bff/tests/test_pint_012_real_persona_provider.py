from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


from services.control_plane.bff.agora.interaction.runner import run_selected_persona_interaction
from services.control_plane.bff.agora.interaction.provider import (
    RecommendedMeasure,
    authority_boundary,
    build_participant_admission,
    build_provider_prompt,
)
from services.control_plane.bff.agora.interaction.router import SubmitInteractionRequest
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClientError


AUTHORITY_KEYS = {
    "order_submitted",
    "broker_called",
    "capital_changed",
    "runtime_bound",
    "lifecycle_promoted",
    "policy_mutated",
    "persona_memory_mutated",
}


class ReadStore:
    def list_personas(self, **_kwargs):
        return [
            {
                "persona_id": "alpha",
                "name": "Alpha",
                "tenant_id": "pantheon-dev",
                "persona_version": "alpha-v7",
                "lifecycle_state": "active",
                "environment_ceiling": "paper",
                "mandate": "trend",
                "archetype": "trend_follower",
                "strategy_family": "momentum",
            },
            {
                "persona_id": "risk",
                "name": "Risk",
                "tenant_id": "pantheon-dev",
                "persona_version": "risk-v3",
                "lifecycle_state": "active",
                "environment_ceiling": "paper",
                "mandate": "drawdown containment",
                "archetype": "risk_manager",
                "strategy_family": "risk_control",
            },
        ]

    def get_capability_snapshot_for_persona(self, persona_id):
        return {
            "snapshot_id": f"snapshot-{persona_id}",
            "persona_id": persona_id,
            "capabilities": ["persona_opinion"],
        }


class WorkshopStore:
    def __init__(self):
        self.events = []
        self.cards = []

    def create_event(self, event):
        self.events.append(event)

    def record_workshop_card(self, card):
        self.cards.append(card)


class Provider:
    def __init__(self, *, fail_persona=None, invalid=False):
        self.fail_persona = fail_persona
        self.invalid = invalid
        self.ensures = []
        self.calls = []

    def ensure_persona_opinion_agent(self, admission, *, persona_profile):
        self.ensures.append((admission, persona_profile))
        assert admission["execution_authority"] == "none"
        assert admission["allowed_capabilities"] == ["persona_opinion"]
        return {
            "agent": {"agent_id": admission["agent_id"]},
            "execution_authority": "none",
        }

    def invoke_assistant_provider(self, **kwargs):
        self.calls.append(kwargs)
        participant = kwargs["context_pack"]["participant"]
        persona_id = participant["persona_id"]
        if persona_id == self.fail_persona:
            raise OpenClawOpsClientError(
                "provider unavailable",
                status_code=503,
                error_code="OPENCLAW_UNAVAILABLE",
            )
        if self.invalid:
            text = json.dumps({"rationale": "missing required typed fields"})
        else:
            text = json.dumps({
                "conclusion": "support" if persona_id == "alpha" else "oppose",
                "rationale": f"unique provider reasoning from {persona_id}",
                "confidence": 0.71 if persona_id == "alpha" else 0.62,
                "uncertainty": [f"{persona_id}-uncertainty"],
                "risks": [f"{persona_id}-risk"],
                "invalidation_conditions": [f"{persona_id}-invalidated"],
                "evidence_refs": [],
                "recommended_measures": [],
            })
        return {
            "status": "ok",
            "data": {
                "provider": "openclaw",
                "status": "completed",
                "output": {
                    "agent_id": kwargs["agent_id"],
                    "request_id": f"response-{persona_id}",
                    "json_events": [{
                        "type": "item.completed",
                        "item": {"text": text},
                    }],
                },
            },
        }


def run(provider, participants=("alpha", "risk")):
    workshop = WorkshopStore()
    result = run_selected_persona_interaction(
        workshop_store=workshop,
        read_store=ReadStore(),
        workshop_id="ws-1",
        interaction_id="int-1",
        topic="Challenge the thesis using the frozen journal and strategy context",
        mode="challenge",
        participants=list(participants),
        context_refs=[
            {"type": "strategy", "id": "strategy-1", "version_id": "v9"},
            {"type": "journal_entry", "id": "journal-7", "version_id": None},
        ],
        environment="paper",
        tenant_id="pantheon-dev",
        user_id="operator-user",
        operator_id="operator-user",
        trace_id="trace-int-1",
        occurred_at="2026-07-17T10:00:00Z",
        client_factory=lambda: provider,
    )
    return result, workshop


def test_each_frozen_persona_invokes_a_distinct_admitted_agent_with_exact_context():
    provider = Provider()
    result, workshop = run(provider)

    assert result["status"] == "completed"
    assert len(result["opinions"]) == 2
    assert {opinion["rationale"] for opinion in result["opinions"]} == {
        "unique provider reasoning from alpha",
        "unique provider reasoning from risk",
    }
    assert result["synthesis"]["status"] == "no_consensus"
    assert result["synthesis"]["opinion_ids"] == [
        opinion["opinion_id"] for opinion in result["opinions"]
    ]
    assert len({call["agent_id"] for call in provider.calls}) == 2
    for call in provider.calls:
        context = call["context_pack"]
        admission = call["persona_admission"]
        frozen = context["frozen_persona_profile"]
        assert call["agent_id"] == context["participant"]["provider_agent_id"]
        assert admission["persona_version"] == context["participant"]["persona_version"]
        assert admission["tenant_id"] == "pantheon-dev"
        assert admission["requested_environment"] == "paper"
        assert admission["display_name"] == frozen["display_name"]
        assert admission["mandate"] == frozen["mandate"]
        assert admission["archetype"] == frozen["archetype"]
        assert admission["strategy_family"] == frozen["strategy_family"]
        assert context["context_refs"][0]["version_id"] == "v9"
        assert frozen["persona_id"] == context["participant"]["persona_id"]
        assert frozen["persona_version"] == context["participant"]["persona_version"]
        assert frozen["display_name"]
        assert frozen["mandate"]
        assert frozen["archetype"]
        assert frozen["strategy_family"]
        assert context["immutable_human_request"]["request_text"].startswith("Challenge the thesis")
        assert "IMMUTABLE_TYPED_CONTEXT=" in call["prompt"]
        assert call["metadata"]["allowed_tools"] == []
    offered = [event for event in workshop.events if event["event_type"] == "opinion_offered"]
    assert len(offered) == 2
    assert all("priv-content-stub" not in event["private_content_ref"] for event in offered)


def test_provider_prompt_exposes_the_complete_strict_recommended_measure_contract():
    prompt, _context = build_provider_prompt(
        topic="Recommend one bounded paper risk limit",
        mode="challenge",
        interaction_id="int-contract",
        participant={
            "persona_id": "risk",
            "persona_version": "risk-v3",
            "display_name": "Risk",
        },
        persona_profile={
            "mandate": "drawdown containment",
            "archetype": "risk_manager",
            "strategy_family": "risk_control",
        },
        context_refs=[{"type": "strategy", "id": "strategy-1", "version_id": "v9"}],
        tenant_id="pantheon-dev",
        submitted_at="2026-07-21T04:00:00Z",
    )

    encoded_shape = prompt.split("Required output shape: ", 1)[1].splitlines()[0]
    shape = json.loads(encoded_shape)
    measure = shape["recommended_measures"][0]
    assert set(measure) == set(RecommendedMeasure.model_fields)
    assert set(measure["target"]) == {"kind", "id", "version", "path"}
    assert set(measure["validation_plan"]) == {"validator", "required_checks"}
    assert set(measure["evidence_refs"][0]) == {
        "ref_type", "ref_id", "version", "observed_at", "data_cutoff", "freshness", "summary",
    }
    assert measure["authority"] == authority_boundary()
    assert "Do not return measure_sha256" in prompt


def test_exact_admission_rejects_cross_tenant_and_environment_above_ceiling():
    persona = ReadStore().list_personas()[0]
    snapshot = ReadStore().get_capability_snapshot_for_persona("alpha")
    with pytest.raises(ValueError, match="tenant"):
        build_participant_admission(
            persona=persona, capability_snapshot=snapshot, environment="paper",
            tenant_id="other-tenant", captured_at="2026-07-17T10:00:00Z",
        )
    with pytest.raises(ValueError, match="ceiling"):
        build_participant_admission(
            persona={**persona, "environment_ceiling": "research"},
            capability_snapshot=snapshot, environment="paper",
            tenant_id="pantheon-dev", captured_at="2026-07-17T10:00:00Z",
        )


def test_submit_rejects_duplicate_persona_ids_before_any_provider_side_effect():
    with pytest.raises(ValueError, match="must be unique"):
        SubmitInteractionRequest.model_validate({
            "workshop_id": "ws-1",
            "mode": "challenge",
            "environment": "paper",
            "topic": "Challenge this thesis",
            "participant_persona_ids": ["alpha", "alpha"],
            "context_refs": [{"type": "strategy", "id": "strategy-1", "version_id": "v9"}],
        })


def test_partial_provider_failure_preserves_success_and_never_forges_missing_opinion():
    result, workshop = run(Provider(fail_persona="risk"))

    assert result["status"] == "degraded"
    assert [opinion["participant"]["persona_id"] for opinion in result["opinions"]] == ["alpha"]
    assert result["missing_participant_ids"] == ["risk"]
    failed = [event for event in workshop.events if event["event_type"] == "provider_invocation_failed"]
    assert len(failed) == 1
    assert failed[0]["payload_refs_json"]["opinion"] is None
    assert result["synthesis"]["opinion_ids"] == [result["opinions"][0]["opinion_id"]]


def test_invalid_provider_json_fails_closed_with_no_fake_opinion_or_authority():
    result, workshop = run(Provider(invalid=True), participants=("alpha",))

    assert result["status"] == "failed"
    assert result["opinions"] == []
    assert result["synthesis"] is None
    card = workshop.cards[0]
    assert card["payload"]["opinions"] == []
    assert card["payload"]["missing_participant_ids"] == ["alpha"]
    for invocation in result["invocations"]:
        assert invocation["authority"]["execution_authority"] == "none"
        assert all(invocation["authority"][key] is False for key in AUTHORITY_KEYS)


def test_production_interaction_router_contains_no_keyword_simulator():
    router_source = (
        Path(__file__).resolve().parents[1] / "agora" / "interaction" / "router.py"
    ).read_text(encoding="utf-8")
    assert "simulate_interaction_debate_and_synthesis" not in router_source
    assert '"no_consensus" in topic.lower()' not in router_source
    assert '"degraded" in topic.lower()' not in router_source
    assert "priv-content-stub://" not in router_source
