import services.evolution.auto_consumer_runtime as rt
from services.evolution.test_governed_persona_lifecycle import FakeWorld

ENV = {
    "EVOLUTION_AUTO_CONSUMER_ENABLED": "true", "EVOLUTION_API_URL": "http://evo",
    "AUTO_CONSUMER_PERSONA_URL": "http://per", "AUTO_CONSUMER_GOVERNANCE_URL": "http://gov",
    "AUTO_CONSUMER_PROPOSER_TOKEN": "tp", "AUTO_CONSUMER_PROPOSER_ID": "proposer",
    "AUTO_CONSUMER_GATE_TOKEN": "tg", "AUTO_CONSUMER_GATE_ID": "gate", "AUTO_CONSUMER_PERSONA_TOKEN": "tper",
}


def test_disabled_by_default():
    assert not rt.enabled({}) and rt.enabled(ENV)


def test_scope_requires_exactly_one_tenant_owned_persona_naming_the_artifact():
    rows = [{"persona_id": "p1", "tenant_id": "t1", "lifecycle_state": "paper_owner",
             "metadata": {rt.DISCOVERY_ARTIFACT_KEY: "art-1", rt.DISCOVERY_KIND_KEY: "factor_rule_v1"}}]
    scope = rt.PersonaOwnerScope("http://per", lambda: "t", lambda *a: (200, rows))
    got = scope.resolve({"tenant_id": "t1", "target_id": "art-1"})
    assert got["persona_id"] == "p1" and got["stage"] == "paper"
    assert scope.resolve({"tenant_id": "other", "target_id": "art-1"}) is None
    assert scope.resolve({"tenant_id": "t1", "target_id": "art-x"}) is None


def test_tick_wiring_runs_clean_against_empty_queue():
    world = FakeWorld()
    decision = {"decision_id": "evo-1", "tenant_id": "t1", "action_type": "retire", "target_id": "art-1"}
    persona_rows = [{"persona_id": "p1", "tenant_id": "t1", "lifecycle_state": "paper_owner",
                     "metadata": {rt.DISCOVERY_ARTIFACT_KEY: "art-1", rt.DISCOVERY_KIND_KEY: "factor_rule_v1"}}]

    def transport(method, url, headers, body):
        if url == "http://per/api/personas":
            return 200, persona_rows
        if "/api/evolution/proposals" in url and method == "GET":
            return 200, []
        return world(method, url, headers, body)

    report = rt.run_consumer_tick(ENV, transport)
    assert report["errors"] == {} and report["retired"] == []
