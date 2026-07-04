from __future__ import annotations

import json

from services.persona.runtime_profile import (
    DEFAULT_PERSONA_MODEL,
    build_persona_runtime_profile,
)


PERSONA = {
    "persona_id": "persona-macro",
    "name": "Macro Persona",
    "metadata": {
        "traits": {"decision_style": "macro signal driven"},
    },
}


def _routing(profile):
    return profile.to_dict()["model_routing"]


def test_runtime_profile_defaults_to_pool_default_and_memory_plane() -> None:
    profile = build_persona_runtime_profile(PERSONA).to_dict()

    assert profile["persona_id"] == "persona-macro"
    assert profile["workspace_ref"].endswith("/persona-macro")
    assert profile["model_routing"]["mode"] == "pool_default"
    assert profile["model_routing"]["status"] == "ready"
    assert profile["model_routing"]["primary_model"] == DEFAULT_PERSONA_MODEL
    assert profile["memory_policy"]["source"] == "canonical_persona_memory_plane"
    assert profile["memory_policy"]["canonical_store"].endswith("PersonaMemoryStore")
    assert profile["memory_policy"]["cache_mutation_policy"] == "memory_bridge_only"
    assert profile["memory_policy"]["direct_session_writes"] is False


def test_runtime_profile_accepts_preferred_pool_model_from_persona_metadata() -> None:
    profile = build_persona_runtime_profile(
        {
            **PERSONA,
            "metadata": {
                "model_routing": {
                    "mode": "preferred_pool_model",
                    "preferred_model": "openai/gpt-5.5",
                },
            },
        }
    )

    routing = _routing(profile)
    assert routing["mode"] == "preferred_pool_model"
    assert routing["status"] == "ready"
    assert routing["primary_model"] == "openai/gpt-5.5"
    assert routing["fallback_models"]
    assert routing["requested_models"] == ["openai/gpt-5.5"]


def test_runtime_profile_accepts_hard_pin_from_route_policy() -> None:
    profile = build_persona_runtime_profile(
        PERSONA,
        route_policy={
            "personaId": "persona-macro",
            "model_routing": {
                "mode": "hard_pin",
                "model": "anthropic/claude-sonnet-4-6",
            },
        },
    )

    routing = _routing(profile)
    assert routing["mode"] == "hard_pin"
    assert routing["status"] == "ready"
    assert routing["primary_model"] == "anthropic/claude-sonnet-4-6"
    assert routing["fallback_models"] == []
    assert routing["selected_provider_pool_ref"].endswith("anthropic/claude-sonnet-4-6")


def test_runtime_profile_accepts_ordered_fallback_route() -> None:
    profile = build_persona_runtime_profile(
        PERSONA,
        route_policy={
            "personaId": "persona-macro",
            "runtime_profile": {
                "model_routing": {
                    "mode": "fallback",
                    "fallback_models": [
                        "anthropic/claude-sonnet-4-6",
                        "openai/gpt-5.5",
                    ],
                },
            },
        },
    )

    routing = _routing(profile)
    assert routing["mode"] == "fallback"
    assert routing["status"] == "ready"
    assert routing["primary_model"] == "anthropic/claude-sonnet-4-6"
    assert routing["fallback_models"] == ["openai/gpt-5.5"]
    assert routing["reason"] == "fallback_order"


def test_runtime_profile_fails_closed_for_unknown_provider_ref() -> None:
    profile = build_persona_runtime_profile(
        PERSONA,
        route_policy={
            "personaId": "persona-macro",
            "model_routing": {
                "mode": "hard_pin",
                "model": "unknown/provider-model",
            },
        },
    )

    routing = _routing(profile)
    assert routing["status"] == "degraded"
    assert routing["primary_model"] is None
    assert routing["fallback_models"] == []
    assert routing["blocked_reason"] == "unknown_model_ref"
    assert routing["invalid_refs"] == ["unknown/provider-model"]


def test_runtime_profile_payload_does_not_expose_auth_material() -> None:
    profile = build_persona_runtime_profile(PERSONA).to_dict()
    payload = json.dumps(profile)

    forbidden = ("api_key", "secret", "token", "credential", "oauth")
    assert not any(term in payload.lower() for term in forbidden)
