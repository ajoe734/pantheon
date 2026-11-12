"""Runtime wiring: scope from real allocator records, decay telemetry -> retire, through the real evolution service."""

import json

from fastapi.testclient import TestClient

import services.evolution.auto_consumer_runtime as rt
from services.evolution.test_governed_persona_lifecycle import FakeWorld

TENANT = "default"
ENV = {
    "EVOLUTION_AUTO_CONSUMER_ENABLED": "true", "EVOLUTION_API_URL": "http://evo",
    "EVOLUTION_SCHEDULER_TENANT_ID": TENANT,
    "AUTO_CONSUMER_PERSONA_URL": "http://per", "AUTO_CONSUMER_GOVERNANCE_URL": "http://gov",
    "AUTO_CONSUMER_CAPITAL_URL": "http://cap", "AUTO_CONSUMER_REGISTRY_URL": "http://reg",
    "AUTO_CONSUMER_PROPOSER_TOKEN": "tp", "AUTO_CONSUMER_PROPOSER_ID": "proposer",
    "AUTO_CONSUMER_GATE_TOKEN": "tg", "AUTO_CONSUMER_GATE_ID": "gate", "AUTO_CONSUMER_PERSONA_TOKEN": "tper",
    "AUTO_CONSUMER_CAPITAL_TOKEN": "tcap", "AUTO_CONSUMER_REGISTRY_TOKEN": "treg",
}

SPEC_ID = "spec-disc-1"
ARTIFACT_ID = "art-disc-1"
BINDING_ID = "pcb-discovery-abc123"


def _binding(**over):
    # Exactly the shape the discovery allocator's provision() creates: no persona metadata markers.
    row = {"binding_id": BINDING_ID, "tenant_id": TENANT, "persona_id": "p1", "status": "active", "budget": 10000,
           "metadata": {"strategy_spec_registry_id": SPEC_ID, "tenant_id": TENANT}}
    row.update(over)
    return row


def _owners(bindings=None, persona_state="paper_owner", kind="factor_rule_v1", persona_tenant=TENANT,
            artifact_persona=None):
    bindings = [_binding()] if bindings is None else bindings
    persona = {"persona_id": "p1", "tenant_id": persona_tenant, "lifecycle_state": persona_state, "metadata": {}}

    def call(method, url, headers, body):
        if url == "http://cap/api/bindings":
            return 200, bindings
        if url == f"http://reg/api/registry/strategy-artifacts/{ARTIFACT_ID}":
            meta = {"persona_id": artifact_persona} if artifact_persona else {}
            return 200, {"entry": {"registry_id": ARTIFACT_ID, "owner_tenant": TENANT, "metadata": {
                **meta, "strategy_artifact": {"strategy_logic": {"kind": kind}, "lineage": {"source_strategy_spec_id": SPEC_ID}}}}}
        if url.startswith("http://reg/"):
            return 404, {"detail": "nf"}
        if url == "http://per/api/personas/p1":
            return 200, persona
        raise AssertionError(url)

    return call, persona


def _scope(call):
    return rt.DiscoveryBindingScope(capital_url="http://cap", registry_url="http://reg", persona_url="http://per",
                                    capital_token=lambda: "c", registry_token=lambda: "r", persona_token=lambda: "p",
                                    transport=call)


def test_disabled_by_default():
    assert not rt.enabled({}) and rt.enabled(ENV)


def test_allocated_persona_resolves_without_any_persona_metadata_injection():
    call, _ = _owners()
    got = _scope(call).resolve({"tenant_id": TENANT, "target_id": ARTIFACT_ID})
    assert got["persona_id"] == "p1" and got["stage"] == "paper" and got["strategy_kind"] == "factor_rule_v1"
    # the admitted spec itself is also an in-scope target
    assert _scope(call).resolve({"tenant_id": TENANT, "target_id": SPEC_ID})["persona_id"] == "p1"


def test_scope_fails_closed():
    for kw in ({"bindings": []},
               {"bindings": [_binding(status="retired")]},
               {"bindings": [_binding(binding_id="pcb-manual-1")]},
               {"bindings": [_binding(), _binding(binding_id="pcb-discovery-zzz", persona_id="p2")]},
               {"persona_tenant": "other"}):
        call, _ = _owners(**kw)
        assert _scope(call).resolve({"tenant_id": TENANT, "target_id": ARTIFACT_ID}) is None, kw
    call, _ = _owners()
    assert _scope(call).resolve({"tenant_id": "other", "target_id": ARTIFACT_ID}) is None


def test_explicit_persona_mismatch_fails_closed():
    # artifact metadata names a non-discovery persona while the only discovery binding belongs to p1
    call, _ = _owners(artifact_persona="non-discovery-persona-2")
    assert _scope(call).resolve({"tenant_id": TENANT, "target_id": ARTIFACT_ID}) is None
    from services.evolution.auto_consumer import evaluate_gate
    verdict = evaluate_gate({"decision_id": "d1", "decision_state": "proposed", "action_type": "retrain",
                             "tenant_id": TENANT, "target_id": ARTIFACT_ID}, _scope(call))
    assert verdict.eligible is False
    # agreeing artifact persona and a conflicting decision persona
    call, _ = _owners(artifact_persona="p1")
    assert _scope(call).resolve({"tenant_id": TENANT, "target_id": ARTIFACT_ID})["persona_id"] == "p1"
    assert _scope(call).resolve({"tenant_id": TENANT, "target_id": ARTIFACT_ID, "persona_id": "p9"}) is None


def test_decision_reads_bind_the_configured_tenant_without_transport_help():
    seen = []

    def transport(method, url, headers, body):
        seen.append((method, url, headers.get("X-Tenant-Id")))
        return (200, []) if headers.get("X-Tenant-Id") else (400, {"detail": "X-Tenant-Id is required"})

    port = rt.HttpDecisionPort("http://evo", lambda: "tok", transport, TENANT)
    for state in ("proposed", "reviewed", "approved"):
        assert port.list_by_state(state) == []
    assert [t for _, _, t in seen] == [TENANT] * 3
    try:
        rt.HttpDecisionPort("http://evo", lambda: "tok", transport).list_by_state("proposed")
    except RuntimeError:
        pass
    else:
        raise AssertionError("an unbound tenant must fail closed")
