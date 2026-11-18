from __future__ import annotations

import json

from services.persona.runtime_profile import build_persona_runtime_profile

# Fixture values of the chain variable; nothing in code names a model.
CHAIN = ["fx-a/model-1", "fx-b/model-2", "fx-b/model-3"]
CLI_REF = "fx-c/cli-model"
POOL = {
    "fx-a/model-1": {"agentRuntime": {"id": "openclaw"}},
    "fx-b/model-2": {"agentRuntime": {"id": "openclaw"}},
    "fx-b/model-3": {"agentRuntime": {"id": "openclaw"}},
    CLI_REF: {"agentRuntime": {"id": "fx-cli"}},
}

PERSONA = {
    "persona_id": "persona-macro",
    "name": "Macro Persona",
    "metadata": {
        "traits": {"decision_style": "macro signal driven"},
    },
}


def _routing(profile):
    return profile.to_dict()["model_routing"]


def _build(persona=PERSONA, **kwargs):
    kwargs.setdefault("model_chain", CHAIN)
    return build_persona_runtime_profile(persona, **kwargs)


def test_runtime_profile_defaults_to_pool_default_and_memory_plane() -> None:
    profile = _build().to_dict()

    assert profile["persona_id"] == "persona-macro"
    assert profile["workspace_ref"].endswith("/persona-macro")
    assert profile["model_routing"]["mode"] == "pool_default"
    assert profile["model_routing"]["status"] == "ready"
    assert profile["model_routing"]["primary_model"] == CHAIN[0]
    assert profile["model_routing"]["fallback_models"] == CHAIN[1:]
    assert profile["memory_policy"]["source"] == "canonical_persona_memory_plane"
    assert profile["memory_policy"]["canonical_store"].endswith("PersonaMemoryStore")
    assert profile["memory_policy"]["cache_mutation_policy"] == "memory_bridge_only"
    assert profile["memory_policy"]["direct_session_writes"] is False


def test_runtime_profile_chain_defaults_from_env_then_default_file(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_MODEL_CHAIN", "env-a/one, env-b/two")
    routing = _routing(build_persona_runtime_profile(PERSONA))
    assert routing["primary_model"] == "env-a/one"
    assert routing["fallback_models"] == ["env-b/two"]

    monkeypatch.delenv("OPENCLAW_MODEL_CHAIN")
    from services.persona.runtime_profile import default_chain

    routing = _routing(build_persona_runtime_profile(PERSONA))
    assert [routing["primary_model"], *routing["fallback_models"]] == default_chain()


def test_runtime_profile_accepts_preferred_pool_model_from_persona_metadata() -> None:
    profile = _build(
        {
            **PERSONA,
            "metadata": {
                "model_routing": {
                    "mode": "preferred_pool_model",
                    "preferred_model": CHAIN[2],
                },
            },
        }
    )

    routing = _routing(profile)
    assert routing["mode"] == "preferred_pool_model"
    assert routing["status"] == "ready"
    assert routing["primary_model"] == CHAIN[2]
    assert routing["fallback_models"] == CHAIN[:2]
    assert routing["requested_models"] == [CHAIN[2]]


def test_runtime_profile_accepts_hard_pin_from_route_policy() -> None:
    profile = _build(
        route_policy={
            "personaId": "persona-macro",
            "model_routing": {"mode": "hard_pin", "model": CHAIN[1]},
        },
    )

    routing = _routing(profile)
    assert routing["mode"] == "hard_pin"
    assert routing["status"] == "ready"
    assert routing["primary_model"] == CHAIN[1]
    assert routing["fallback_models"] == []
    assert routing["selected_provider_pool_ref"].endswith(CHAIN[1])


def test_runtime_profile_accepts_ordered_fallback_route() -> None:
    profile = _build(
        route_policy={
            "personaId": "persona-macro",
            "runtime_profile": {
                "model_routing": {
                    "mode": "fallback",
                    "fallback_models": [CHAIN[1], CHAIN[2]],
                },
            },
        },
    )

    routing = _routing(profile)
    assert routing["mode"] == "fallback"
    assert routing["status"] == "ready"
    assert routing["primary_model"] == CHAIN[1]
    assert routing["fallback_models"] == [CHAIN[2]]
    assert routing["reason"] == "fallback_order"


def test_runtime_profile_fails_closed_for_unknown_provider_ref() -> None:
    profile = _build(
        route_policy={
            "personaId": "persona-macro",
            "model_routing": {"mode": "hard_pin", "model": "unknown/provider-model"},
        },
    )

    routing = _routing(profile)
    assert routing["status"] == "degraded"
    assert routing["primary_model"] is None
    assert routing["fallback_models"] == []
    assert routing["blocked_reason"] == "unknown_model_ref"
    assert routing["invalid_refs"] == ["unknown/provider-model"]


def test_route_policy_validates_against_gateway_registered_models() -> None:
    # A ref outside the chain is valid when the gateway has it registered...
    extra = {**POOL, "fx-d/extra": {"agentRuntime": {"id": "openclaw"}}}
    policy = {"model_routing": {"mode": "hard_pin", "model": "fx-d/extra"}}
    routing = _routing(_build(route_policy=policy, provider_pool=extra))
    assert routing["status"] == "ready"
    assert routing["primary_model"] == "fx-d/extra"
    # ...and invalid when it is not registered, even if the chain names it.
    routing = _routing(_build(route_policy={"model_routing": {"mode": "hard_pin", "model": CHAIN[0]}}, provider_pool={"fx-b/model-2": {}}))
    assert routing["status"] == "degraded"
    assert routing["blocked_reason"] == "unknown_model_ref"


def test_runtime_profile_payload_does_not_expose_auth_material() -> None:
    payload = json.dumps(_build().to_dict())

    forbidden = ("api_key", "secret", "token", "credential", "oauth")
    assert not any(term in payload.lower() for term in forbidden)


def test_fallbacks_follow_chain_only_and_exclude_cli_runtime_models() -> None:
    chain = [CHAIN[0], CLI_REF, CHAIN[1]]
    routing = _routing(_build(provider_pool=POOL, model_chain=chain))
    assert routing["primary_model"] == CHAIN[0]
    assert routing["fallback_models"] == [CHAIN[1]]  # CLI ref skipped, unlisted pool ref not added
    assert CLI_REF not in routing["fallback_models"]
