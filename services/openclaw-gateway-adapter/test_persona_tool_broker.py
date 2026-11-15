from __future__ import annotations

import copy
import json

import pytest

from integrations.openclaw.persona_agent_sync import PERSONA_AGENT_TOOL_PROFILE
from persona_tool_broker import (
    BrokerConfig,
    BrokerRefusal,
    MemoryCallError,
    PersonaToolBroker,
)

TOKEN = "broker-secret"
ALPHA, BETA = "persona-alpha", "persona-beta"
SESSION = "tenant|op|run-1"
SESSION_KEY = "agent:persona-alpha:openai-user:" + SESSION


def _profile():
    return copy.deepcopy(PERSONA_AGENT_TOOL_PROFILE)


class Registry:
    def __init__(self):
        self.agents = {
            ALPHA: {"id": ALPHA, "tools": _profile()},
            BETA: {"id": BETA, "tools": _profile()},
            "main": {"id": "main"},
            "memory-index": {"id": "memory-index", "tools": {"allow": [], "deny": ["*"],
                                                              "exec": {"security": "deny", "ask": "always"}}},
        }
        self.reads = 0

    def __call__(self, agent_id):
        self.reads += 1
        return self.agents.get(agent_id)


class Memory:
    """Stands in for memory-svc; records each request so scope can be asserted."""

    def __init__(self):
        self.requests = []
        self.fail = None

    def __call__(self, path, params, *, actor_roles=None):
        self.requests.append((path, dict(params)))
        if self.fail:
            raise self.fail
        if path == "/api/memory/persona-standing-questions":
            return {"persona_id": params["persona_id"], "derived": True,
                    "answers": [{"key": "risk_lessons", "text": "reduce size", "citations": ["pm-1"]}]}
        if params["scope"] == "institutional":
            return {"hits": [{"type": "institutional", "relevance_score": 1.0, "entry": {
                "entry_id": "inst-1", "content": {"headline": "h", "body": "b", "tags": []}}}]}
        committee = params["actor_roles"] == ["consultation_session"]
        hits = [{"type": "persona", "relevance_score": 2.0, "entry": {
            "memory_id": "pm-committee", "persona_id": params["persona_id"], "relevance_scope": "persona_and_committee",
            "content": {"summary": "shareable", "tags": []}}}]
        if not committee:
            hits.append({"type": "persona", "relevance_score": 1.0, "entry": {
                "memory_id": "pm-private", "persona_id": params["persona_id"], "relevance_scope": "persona_private",
                "content": {"summary": "private", "tags": []}}})
        return {"hits": hits, "retrieval_mode": "hindsight"}


@pytest.fixture
def env():
    registry, memory = Registry(), Memory()
    broker = PersonaToolBroker(
        BrokerConfig(enabled=True, token=TOKEN, registry_ttl_seconds=0.0,
                     limits={"persona_memory_recall": 20, "persona_mental_model_read": 20, "persona_proposal_submit": 20}),
        agent_lookup=registry, memory_get=memory,
    )
    return broker, registry, memory


@pytest.fixture
def limited():
    registry, memory = Registry(), Memory()
    broker = PersonaToolBroker(
        BrokerConfig(enabled=True, token=TOKEN, registry_ttl_seconds=0.0,
                     limits={"persona_memory_recall": 2, "persona_mental_model_read": 1, "persona_proposal_submit": 2}),
        agent_lookup=registry, memory_get=memory,
    )
    return broker, registry, memory


def call(broker, tool, params=None, *, agent=ALPHA, run_id="run-a", session_key=SESSION_KEY, auth=f"Bearer {TOKEN}"):
    return broker.handle(authorization=auth, body={"tool": tool, "params": params or {}, "agent_id": agent,
                                                   "run_id": run_id, "session_key": session_key})


def register(broker, purpose="daily_cycle", persona=ALPHA, session=SESSION):
    return broker.register_run(persona, purpose, session)["run_ref"]


PROPOSAL = {"kind": "no_action", "reason": "nothing to change", "evidence_refs": [],
            "case_file_item_ids": [], "none_applicable_reason": "no case file item applies"}


def test_own_bank_recall_reaches_memory_only_for_the_resolved_persona(env):
    broker, _, memory = env
    ref = register(broker)
    resp = call(broker, "persona_memory_recall", {"query": "momentum"})
    assert resp.status_code == 200
    path, params = memory.requests[-1]
    assert (path, params["scope"], params["persona_id"]) == ("/api/memory/retrieve", "persona", ALPHA)
    assert [h["source_id"] for h in resp.body["result"]["hits"]] == ["pm-committee", "pm-private"]
    record = broker.run_record(ref)
    assert record["calls"][0]["canonical_ids"] == ["persona_memory:pm-committee", "persona_memory:pm-private"]
    assert record["calls"][0]["outcome"] == "ok" and record["persona_id"] == ALPHA and record["run_id"] == "run-a"


def test_the_agent_cannot_choose_another_persona_or_widen_the_request(env):
    broker, _, memory = env
    register(broker)
    for extra in ({"persona_id": BETA}, {"scope": "both"}, {"bank": "persona-beta"}, {"actor_roles": ["admin"]}):
        resp = call(broker, "persona_memory_recall", {"query": "q", **extra})
        assert resp.status_code == 422 and resp.body["reason"] == "invalid_parameters"
    assert memory.requests == []


def test_facts_bank_is_a_separate_institutional_call(env):
    broker, _, memory = env
    register(broker)
    resp = call(broker, "persona_memory_recall", {"query": "q", "bank": "facts"})
    assert resp.status_code == 200 and resp.body["result"]["hits"][0]["canonical_ref"] == "institutional_memory:inst-1"
    assert memory.requests[-1][1]["scope"] == "institutional" and "persona_id" not in memory.requests[-1][1]


def test_unregistered_agent_is_refused_before_memory(env):
    broker, _, memory = env
    register(broker)
    for agent in ("main", "memory-index", "ghost"):
        resp = call(broker, "persona_memory_recall", {"query": "q"}, agent=agent)
        assert resp.status_code == 403
        assert resp.body["reason"] in {"unregistered_agent", "not_a_persona_agent"}
    assert memory.requests == []


def test_tool_outside_the_profile_and_denied_tool_are_refused(env):
    broker, registry, memory = env
    register(broker)
    assert call(broker, "exec", {"cmd": "ls"}).body["reason"] == "tool_outside_profile"
    assert call(broker, "persona_reflect", {"query": "q"}).body["reason"] == "tool_outside_profile"
    # deny-first: a tool that is both allowed and denied is refused
    registry.agents[ALPHA]["tools"]["deny"].append("persona_memory_recall")
    # a profile that no longer matches the governed persona profile is not a persona agent at all
    resp = call(broker, "persona_memory_recall", {"query": "q"})
    assert resp.status_code == 403 and resp.body["reason"] in {"tool_outside_profile", "not_a_persona_agent"}
    assert memory.requests == []


def test_deny_wins_over_allow_in_the_profile_check():
    profile = _profile()
    profile["deny"].append("persona_memory_recall")
    assert PersonaToolBroker._profile_allows(profile, "persona_memory_recall") is False
    assert PersonaToolBroker._profile_allows(_profile(), "persona_memory_recall") is True
    assert PersonaToolBroker._profile_allows({"allow": ["x"], "deny": ["*"]}, "x") is False


def test_call_from_an_unregistered_run_is_refused_and_has_no_run_record(env):
    broker, _, memory = env
    resp = call(broker, "persona_memory_recall", {"query": "q"})
    assert resp.status_code == 403 and resp.body["reason"] == "run_not_registered"
    other = call(broker, "persona_memory_recall", {"query": "q"}, session_key="agent:persona-alpha:openai-user:other")
    assert other.body["reason"] == "run_not_registered"
    assert memory.requests == []
    assert broker.telemetry[-1]["outcome"] == "refused"


def test_a_run_registered_for_one_persona_is_not_usable_by_another(env):
    broker, _, memory = env
    register(broker)
    resp = call(broker, "persona_memory_recall", {"query": "q"}, agent=BETA)
    assert resp.status_code == 403 and resp.body["reason"] == "run_not_registered"
    assert memory.requests == []


def test_registration_requires_a_registered_persona_agent_and_known_purpose(env):
    broker, _, _ = env
    with pytest.raises(BrokerRefusal) as excinfo:
        broker.register_run("main", "daily_cycle", "s1")
    assert excinfo.value.reason == "not_a_persona_agent"
    with pytest.raises(BrokerRefusal) as excinfo:
        broker.register_run("ghost", "daily_cycle", "s1")
    assert excinfo.value.reason == "unregistered_agent"
    with pytest.raises(BrokerRefusal) as excinfo:
        broker.register_run(ALPHA, "free_for_all", "s1")
    assert excinfo.value.reason == "invalid_purpose"
    register(broker)
    with pytest.raises(BrokerRefusal) as excinfo:
        register(broker)
    assert excinfo.value.status_code == 409


def test_a_run_id_is_bound_to_its_registration(env):
    broker, _, _ = env
    register(broker)
    assert call(broker, "persona_memory_recall", {"query": "q"}, run_id="run-a").status_code == 200
    resp = call(broker, "persona_memory_recall", {"query": "q"}, run_id="run-b")
    assert resp.status_code == 403 and resp.body["reason"] == "run_id_mismatch"


def test_panel_interpretation_sees_only_committee_entries_and_facts(env):
    broker, _, memory = env
    ref = register(broker, "panel_interpretation")
    own = call(broker, "persona_memory_recall", {"query": "q"})
    assert own.status_code == 200
    assert [h["source_id"] for h in own.body["result"]["hits"]] == ["pm-committee"]
    assert memory.requests[-1][1]["actor_roles"] == ["consultation_session"]
    assert call(broker, "persona_memory_recall", {"query": "q", "bank": "facts"}).status_code == 200
    for tool, params in (("persona_mental_model_read", {}), ("persona_proposal_submit", PROPOSAL)):
        resp = call(broker, tool, params)
        assert resp.status_code == 403 and resp.body["reason"] == "tool_not_allowed_for_purpose"
    assert len(memory.requests) == 2
    outcomes = [(c["tool"], c["outcome"]) for c in broker.run_record(ref)["calls"]]
    assert outcomes[-2:] == [("persona_mental_model_read", "refused"), ("persona_proposal_submit", "refused")]


def test_panel_run_drops_a_private_entry_even_if_memory_returned_it(env):
    broker, _, memory = env
    register(broker, "panel_interpretation")
    original = memory.__call__

    def leaky(path, params, *, actor_roles=None):
        body = original(path, params, actor_roles=actor_roles)
        body["hits"].append({"type": "persona", "relevance_score": 0.5, "entry": {
            "memory_id": "pm-private", "persona_id": ALPHA, "relevance_scope": "persona_private",
            "content": {"summary": "private", "tags": []}}})
        return body

    broker._memory_get = leaky
    resp = call(broker, "persona_memory_recall", {"query": "q"})
    assert [h["source_id"] for h in resp.body["result"]["hits"]] == ["pm-committee"]
    assert resp.body["result"]["withheld"] == 1
    assert any(e.get("reason") == "not_committee_scope" for e in broker.telemetry)


def test_recall_never_returns_another_personas_entry(env):
    broker, _, memory = env
    register(broker)
    original = memory.__call__

    def crossing(path, params, *, actor_roles=None):
        body = original(path, params, actor_roles=actor_roles)
        body["hits"].append({"type": "persona", "relevance_score": 9.0, "entry": {
            "memory_id": "pm-beta", "persona_id": BETA, "relevance_scope": "persona_private",
            "content": {"summary": "beta secret", "tags": []}}})
        return body

    broker._memory_get = crossing
    resp = call(broker, "persona_memory_recall", {"query": "q"})
    assert "pm-beta" not in json.dumps(resp.body)
    assert resp.body["result"]["withheld"] == 1
    assert any(e.get("withheld") == ["pm-beta"] for e in broker.telemetry)  # recorded, not shown to the agent


def test_mental_model_read_returns_only_own_answers_and_fails_closed_on_mismatch(env):
    broker, _, memory = env
    ref = register(broker)
    resp = call(broker, "persona_mental_model_read")
    assert resp.status_code == 200 and resp.body["result"]["answers"][0]["key"] == "risk_lessons"
    assert memory.requests[-1][1]["persona_id"] == ALPHA
    assert broker.run_record(ref)["calls"][0]["canonical_ids"] == ["pm-1"]
    assert call(broker, "persona_mental_model_read", {"persona_id": BETA}).status_code == 422
    register(broker, session="s2")
    broker._memory_get = lambda path, params, **kw: {"persona_id": BETA, "answers": [{"key": "x", "text": "beta"}]}
    resp = call(broker, "persona_mental_model_read", session_key="x:s2", run_id="run-2")
    assert resp.status_code == 503 and "beta" not in json.dumps(resp.body)


def test_proposals_are_validated_recorded_and_never_applied(env):
    broker, _, memory = env
    ref = register(broker)
    resp = call(broker, "persona_proposal_submit", PROPOSAL)
    assert resp.status_code == 200 and resp.body["result"] == {"recorded": True, "applied": False, "proposal_index": 0}
    assert memory.requests == []
    record = broker.run_record(ref)
    assert record["proposals"][0]["kind"] == "no_action" and record["calls"][0]["outcome"] == "recorded"
    bad_kind = call(broker, "persona_proposal_submit", {**PROPOSAL, "kind": "place_order"})
    both = call(broker, "persona_proposal_submit", {**PROPOSAL, "case_file_item_ids": ["i1"]})
    neither = call(broker, "persona_proposal_submit", {**PROPOSAL, "none_applicable_reason": None})
    extra = call(broker, "persona_proposal_submit", {**PROPOSAL, "order": {"qty": 1}})
    assert [r.status_code for r in (bad_kind, both, neither, extra)] == [422] * 4
    assert len(broker.run_record(ref)["proposals"]) == 1  # refused proposals are not in the record


def test_per_run_call_limits_are_enforced_per_tool(limited):
    broker, _, memory = limited
    ref = register(broker)
    assert [call(broker, "persona_memory_recall", {"query": "q"}).status_code for _ in range(3)] == [200, 200, 429]
    assert call(broker, "persona_mental_model_read").status_code == 200
    assert call(broker, "persona_mental_model_read").status_code == 429
    assert sum(1 for p, _ in memory.requests if p == "/api/memory/retrieve") == 2
    assert broker.run_record(ref)["calls"][2]["reason"] == "run_call_limit_exceeded"
    register(broker, session="s2")  # a fresh run has fresh limits
    assert call(broker, "persona_memory_recall", {"query": "q"}, session_key="x:s2", run_id="run-2").status_code == 200


def test_every_call_is_recorded_with_digest_and_never_the_text(env):
    broker, _, _ = env
    ref = register(broker)
    call(broker, "persona_memory_recall", {"query": "SECRET-QUERY"})
    event = broker.telemetry[-1]
    assert event["persona_id"] == ALPHA and event["run_id"] == "run-a" and event["tool"] == "persona_memory_recall"
    assert len(event["param_digest"]) == 64 and event["outcome"] == "ok"
    assert "SECRET-QUERY" not in json.dumps(event) and "shareable" not in json.dumps(event)
    assert "SECRET-QUERY" not in json.dumps(broker.run_record(ref))


def test_memory_failure_fails_closed_with_a_recorded_reason(env):
    broker, _, memory = env
    ref = register(broker)
    memory.fail = MemoryCallError(503, "llm_layers_disabled")
    resp = call(broker, "persona_memory_recall", {"query": "q"})
    assert resp.status_code == 503 and resp.body["reason"] == "llm_layers_disabled"
    assert broker.run_record(ref)["calls"][0]["outcome"] == "unavailable"
    memory.fail = MemoryCallError(403, "committee_scope_has_no_derived_answers")
    assert call(broker, "persona_mental_model_read").status_code == 403


def test_auth_disabled_and_malformed_bodies(env):
    broker, _, memory = env
    register(broker)
    assert call(broker, "persona_memory_recall", {"query": "q"}, auth="Bearer nope").status_code == 401
    assert call(broker, "persona_memory_recall", {"query": "q"}, auth=None).status_code == 401
    assert broker.handle(authorization=f"Bearer {TOKEN}", body=[]).status_code == 422
    assert broker.handle(authorization=f"Bearer {TOKEN}", body={"tool": "persona_memory_recall"}).status_code == 422
    off = PersonaToolBroker(BrokerConfig(enabled=False, token=TOKEN), agent_lookup=lambda a: None, memory_get=memory)
    assert off.handle(authorization=f"Bearer {TOKEN}", body={}).status_code == 404
    no_token = PersonaToolBroker(BrokerConfig(enabled=True, token=""), agent_lookup=lambda a: None, memory_get=memory)
    assert no_token.handle(authorization="Bearer ", body={}).status_code == 404
    assert memory.requests == []


def test_registry_failure_is_a_refusal_not_a_pass(env):
    broker, registry, memory = env

    def broken(agent_id):
        raise RuntimeError("config unreadable")

    broker._agent_lookup = broken
    resp = call(broker, "persona_memory_recall", {"query": "q"})
    assert resp.status_code == 503 and resp.body["reason"] == "agent_registry_unavailable"
    assert memory.requests == []


def test_authorize_reports_the_run_tool_set_without_side_effects(env):
    broker, _, memory = env
    ref = register(broker, "panel_interpretation")
    resp = broker.handle(authorization=f"Bearer {TOKEN}", authorize_only=True, body={
        "tool": "persona_memory_recall", "params": {}, "agent_id": ALPHA, "run_id": "r", "session_key": SESSION_KEY})
    assert resp.status_code == 200 and resp.body["allowed_tools"] == ["persona_memory_recall"]
    assert broker.run_record(ref)["calls"] == [] and memory.requests == []


def test_registry_is_cached_only_within_the_ttl():
    registry, memory = Registry(), Memory()
    now = [100.0]
    broker = PersonaToolBroker(BrokerConfig(enabled=True, token=TOKEN, registry_ttl_seconds=10.0),
                               agent_lookup=registry, memory_get=memory, clock=lambda: now[0])
    broker.register_run(ALPHA, "daily_cycle", SESSION)
    base = registry.reads
    call(broker, "persona_memory_recall", {"query": "q"})
    call(broker, "persona_memory_recall", {"query": "q"})
    assert registry.reads == base  # served from cache inside the TTL
    now[0] += 11
    del registry.agents[ALPHA]  # the agent is removed from the registry
    resp = call(broker, "persona_memory_recall", {"query": "q"})
    assert resp.status_code == 403 and resp.body["reason"] == "unregistered_agent"


def test_records_persist_across_a_restart(tmp_path):
    cfg = BrokerConfig(enabled=True, token=TOKEN, registry_ttl_seconds=0.0, state_path=str(tmp_path / "ptb.sqlite3"))
    first = PersonaToolBroker(cfg, agent_lookup=Registry(), memory_get=Memory())
    ref = first.register_run(ALPHA, "daily_cycle", SESSION)["run_ref"]
    call(first, "persona_proposal_submit", PROPOSAL)
    second = PersonaToolBroker(cfg, agent_lookup=Registry(), memory_get=Memory())
    assert second.run_record(ref)["proposals"][0]["kind"] == "no_action"


# --- adapter routes ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    import main as adapter
    from fastapi.testclient import TestClient

    registry, memory = Registry(), Memory()
    config = BrokerConfig(enabled=True, token=TOKEN, registry_ttl_seconds=0.0)
    monkeypatch.setattr(adapter, "_PERSONA_TOOL_BROKER_CONFIG", config)
    monkeypatch.setattr(adapter, "_PERSONA_TOOL_BROKER",
                        PersonaToolBroker(config, agent_lookup=registry, memory_get=memory))
    monkeypatch.setattr(adapter, "_ASSISTANT_SERVICE_TOKEN", "svc-token")
    return TestClient(adapter.app), memory


SVC = {"X-Pantheon-Service-Token": "svc-token"}
PLUGIN = {"Authorization": f"Bearer {TOKEN}"}


def test_routes_register_call_and_read_the_per_run_record(client):
    http, memory = client
    reg = http.post("/api/openclaw-adapter/agents/persona-tools/runs", headers=SVC,
                    json={"persona_id": ALPHA, "purpose": "daily_cycle", "session_user": SESSION})
    assert reg.status_code == 201
    run_ref = reg.json()["run"]["run_ref"]
    body = {"tool": "persona_memory_recall", "params": {"query": "momentum"}, "agent_id": ALPHA,
            "run_id": "run-a", "session_key": SESSION_KEY}
    assert http.post("/internal/persona-tools/v1/call", json=body, headers=PLUGIN).status_code == 200
    authorize = http.post("/internal/persona-tools/v1/authorize", json=body, headers=PLUGIN)
    assert authorize.status_code == 200 and "persona_memory_recall" in authorize.json()["allowed_tools"]
    proposal = {**body, "tool": "persona_proposal_submit", "params": PROPOSAL}
    assert http.post("/internal/persona-tools/v1/call", json=proposal, headers=PLUGIN).status_code == 200
    record = http.get(f"/api/openclaw-adapter/agents/persona-tools/runs/{run_ref}", headers=SVC).json()["record"]
    assert [c["tool"] for c in record["calls"]] == ["persona_memory_recall", "persona_proposal_submit"]
    assert record["proposals"][0]["kind"] == "no_action" and len(memory.requests) == 1


def test_internal_routes_need_the_broker_secret_and_run_routes_need_the_service_token(client):
    http, _ = client
    body = {"tool": "persona_memory_recall", "params": {"query": "q"}, "agent_id": ALPHA,
            "run_id": "r", "session_key": SESSION_KEY}
    assert http.post("/internal/persona-tools/v1/call", json=body).status_code == 401
    assert http.post("/internal/persona-tools/v1/call", json=body,
                     headers={"Authorization": "Bearer svc-token"}).status_code == 401
    assert http.post("/internal/persona-tools/v1/call", content=b"not json", headers=PLUGIN).status_code == 422
    reg = {"persona_id": ALPHA, "purpose": "daily_cycle", "session_user": SESSION}
    assert http.post("/api/openclaw-adapter/agents/persona-tools/runs", json=reg).status_code == 401
    assert http.post("/api/openclaw-adapter/agents/persona-tools/runs", json=reg, headers=PLUGIN).status_code == 401
    assert http.get("/api/openclaw-adapter/agents/persona-tools/runs/ptr-x", headers=SVC).status_code == 404


def test_registration_refusals_and_disabled_broker(client, monkeypatch):
    import main as adapter

    http, _ = client
    bad = http.post("/api/openclaw-adapter/agents/persona-tools/runs", headers=SVC,
                    json={"persona_id": "main", "purpose": "daily_cycle", "session_user": "s"})
    assert bad.status_code == 403 and bad.json()["reason"] == "not_a_persona_agent"
    purpose = http.post("/api/openclaw-adapter/agents/persona-tools/runs", headers=SVC,
                        json={"persona_id": ALPHA, "purpose": "x", "session_user": "s"})
    assert purpose.status_code == 422
    monkeypatch.setattr(adapter, "_PERSONA_TOOL_BROKER_CONFIG", BrokerConfig(enabled=False, token=""))
    off = http.post("/api/openclaw-adapter/agents/persona-tools/runs", headers=SVC,
                    json={"persona_id": ALPHA, "purpose": "daily_cycle", "session_user": "s"})
    assert off.status_code == 404
