from __future__ import annotations

import copy
import itertools
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

from integrations.openclaw.persona_agent_sync import PERSONA_AGENT_TOOL_PROFILE, persona_agent_runtime_policy
from persona_tool_broker import (
    BrokerConfig,
    BrokerRefusal,
    MemoryCallError,
    PersonaToolBroker,
    http_memory_get,
)
from services.memory import hindsight_index as hs
from services.memory.test_hindsight_integration import (  # noqa: F401  (fixtures for the opt-in section below)
    _svc, _wait, layered, layered_stack,
)
from services.memory.test_hindsight_index import institution, persona
from services.memory.test_main import JWT_SECRET, _make_jwt

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


_TC = itertools.count(1)


def call(broker, tool, params=None, *, agent=ALPHA, run_id="run-a", session_key=SESSION_KEY, auth=f"Bearer {TOKEN}",
         hook=True):
    """Run the hook's authorize step (records the authorization) then the tool's call, like the plugin does."""
    tool_call_id = f"tc-{next(_TC)}"
    if hook:
        authorized = broker.handle(authorization=auth, authorize_only=True, body={
            "tool": tool, "params": params or {}, "agent_id": agent, "run_id": run_id,
            "session_key": session_key, "tool_call_id": tool_call_id})
        if authorized.status_code != 200:  # the hook would block: the tool never runs
            return authorized
    return broker.handle(authorization=auth, body={"tool": tool, "params": params or {}, "agent_id": agent,
                                                   "session_key": session_key, "tool_call_id": tool_call_id})


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
    assert memory.requests[-1][1]["session_persona_id"] == ALPHA
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


def _authorize(broker, tool_call_id, *, tool="persona_memory_recall", agent=ALPHA, session_key=SESSION_KEY, run_id="run-a"):
    return broker.handle(authorization=f"Bearer {TOKEN}", authorize_only=True, body={
        "tool": tool, "params": {"query": "q"}, "agent_id": agent, "run_id": run_id,
        "session_key": session_key, "tool_call_id": tool_call_id})


def _call(broker, tool_call_id, *, tool="persona_memory_recall", agent=ALPHA, session_key=SESSION_KEY, extra=None):
    return broker.handle(authorization=f"Bearer {TOKEN}", body={
        "tool": tool, "params": {"query": "q"}, "agent_id": agent, "session_key": session_key,
        "tool_call_id": tool_call_id, **(extra or {})})


def test_a_hook_authorization_is_recorded_by_the_broker_and_usable_once(env):
    broker, _, _ = env
    register(broker)
    assert _call(broker, "tc-1").body["reason"] == "call_not_authorized_by_hook"  # nothing recorded
    assert _authorize(broker, "tc-1").status_code == 200
    assert _call(broker, "tc-1").status_code == 200
    assert _call(broker, "tc-1").status_code == 403  # consumed


def test_an_authorization_does_not_serve_another_agent_session_tool_or_run(env):
    broker, _, _ = env
    register(broker)
    assert _authorize(broker, "tc-2").status_code == 200
    assert _call(broker, "tc-2", agent="persona-beta").status_code == 403
    assert _call(broker, "tc-2", session_key=SESSION_KEY + "x").status_code == 403
    assert _call(broker, "tc-2", tool="persona_mental_model_read").status_code == 403  # also consumes it
    assert _call(broker, "tc-2").status_code == 403
    assert _authorize(broker, "tc-3").status_code == 200
    forged = _call(broker, "tc-3", extra={"run_id": "forged"})
    assert forged.status_code == 403 and forged.body["reason"] == "run_id_mismatch"


def test_a_refused_authorize_records_nothing_and_authorizations_expire_and_are_capped(env):
    import persona_tool_broker as ptb

    broker, _, _ = env
    register(broker)
    assert _authorize(broker, "tc-4", tool="persona_reflect").status_code == 403
    assert _call(broker, "tc-4", tool="persona_reflect").status_code == 403
    assert _authorize(broker, "tc-5").status_code == 200
    broker._clock = lambda: time.time() + ptb.AUTHORIZATION_TTL_SECONDS + 5
    assert _call(broker, "tc-5").status_code == 403  # expired
    n = ptb.MAX_AUTHORIZATIONS
    store = broker._store
    for i in range(n + 5):
        store.add_authorization(ALPHA, SESSION_KEY, f"cap-{i}", "persona_memory_recall", "run-a", 1000.0 + i, 1e9, n)
    assert store._db.execute("SELECT count(*) FROM authorizations").fetchone()[0] == n


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
            "run_id": "run-a", "session_key": SESSION_KEY, "tool_call_id": "tc-r1"}
    assert http.post("/internal/persona-tools/v1/call", json=body, headers=PLUGIN).status_code == 403  # no hook yet
    authorize = http.post("/internal/persona-tools/v1/authorize", json=body, headers=PLUGIN)
    assert authorize.status_code == 200 and "persona_memory_recall" in authorize.json()["allowed_tools"]
    assert http.post("/internal/persona-tools/v1/call", json=body, headers=PLUGIN).status_code == 200
    assert http.post("/internal/persona-tools/v1/call", json=body, headers=PLUGIN).status_code == 403  # single use
    proposal = {**body, "tool": "persona_proposal_submit", "params": PROPOSAL, "tool_call_id": "tc-r2"}
    assert http.post("/internal/persona-tools/v1/authorize", json=proposal, headers=PLUGIN).status_code == 200
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


# --- Persona tool path against real containers (design H9 / H10), opt-in -----------------------------
# Persona tool path against real containers (design H9 / H10), opt-in.
#
# Chain under test, every hop real HTTP: the compiled ``pantheon-persona-tools`` plugin (run by node, its
# ``before_tool_call`` hook then ``execute``) -> the real adapter app (broker routes) -> the real memory
# service app (strict JWT) -> a real Hindsight + Postgres container pair (LLM layers on, model calls routed
# through the real adapter memory-LLM bridge into the gateway double used by the memory tests).
#
# Not real here: the OpenClaw gateway process that would load the plugin and the agent registry the broker
# reads (``agents.list``) - both are replaced by the plugin's own hook/execute contract and a registry
# fixture built from the governed ``persona_agent_runtime_policy()``. The gateway-image build and plugin
# load is covered separately in ``docs/deployment/evidence/OPENCLAW-PERSONA-TOOL-BROKER-R2-20261114``.
#
# Opt in with ``PANTHEON_RUN_HINDSIGHT_CONTAINER_PROBE=1`` (starts hindsight + postgres containers; the
# images are built from deploy/hindsight and deploy/memory-index-postgres, see test_hindsight_integration).

PLUGIN_DIR = Path(__file__).resolve().parents[2] / "integrations" / "openclaw" / "plugins" / "pantheon-persona-tools"
BROKER_TOKEN = "it-broker-secret"
SERVICE_TOKEN = "it-service-token"

DRIVER = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const { config, pluginPath, steps } = JSON.parse(readFileSync(0, "utf8"));
const plugin = (await import(pathToFileURL(pluginPath).href)).default;
let hook, factory;
plugin.register({ pluginConfig: config, registerTool(f) { factory = f; }, on(_n, h) { hook = h; } });
const out = [];
let i = 0;
for (const step of steps) {
  i += 1;
  const ctx = { agentId: step.agentId, sessionKey: step.sessionKey, runId: step.runId };
  const event = { toolName: step.tool, params: step.params ?? {}, runId: step.runId, toolCallId: `tc-${i}` };
  const verdict = await hook(event, ctx);
  if (verdict && verdict.block) { out.push({ blocked: true, reason: verdict.blockReason }); continue; }
  const tool = (factory({ agentId: step.agentId, sessionKey: step.sessionKey }) ?? []).find((t) => t.name === step.tool);
  if (!tool) { out.push({ passed: true, exposed: false }); continue; }
  try {
    const result = await tool.execute(event.toolCallId, event.params);
    out.push({ ok: true, details: result.details });
  } catch (error) { out.push({ error: String(error.message) }); }
}
process.stdout.write(JSON.stringify(out));
"""


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app):
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    return server, thread, port


def _build_plugin():
    if not shutil.which("node") or not shutil.which("npm"):
        pytest.fail("node and npm are required for the plugin leg of this integration test")
    if not (PLUGIN_DIR / "node_modules").exists():
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=PLUGIN_DIR, check=True, capture_output=True, text=True)
    subprocess.run(["npm", "run", "build"], cwd=PLUGIN_DIR, check=True, capture_output=True, text=True)
    return PLUGIN_DIR / "dist" / "src" / "index.js"


class Stack:
    def __init__(self, adapter, broker, adapter_port, tenant, ids, tmp_path):
        self.adapter, self.broker, self.adapter_port, self.tenant, self.ids = adapter, broker, adapter_port, tenant, ids
        self.plugin_path = _build_plugin()
        self.tmp = tmp_path

    def register(self, persona_id, purpose, session):
        import httpx

        resp = httpx.post(f"http://127.0.0.1:{self.adapter_port}/api/openclaw-adapter/agents/persona-tools/runs",
                          headers={"X-Pantheon-Service-Token": SERVICE_TOKEN},
                          json={"persona_id": persona_id, "purpose": purpose, "session_user": session})
        assert resp.status_code in (201, 403, 409), resp.text
        return resp

    def record(self, run_ref):
        import httpx

        resp = httpx.get(f"http://127.0.0.1:{self.adapter_port}/api/openclaw-adapter/agents/persona-tools/runs/{run_ref}",
                         headers={"X-Pantheon-Service-Token": SERVICE_TOKEN})
        assert resp.status_code == 200, resp.text
        return resp.json()["record"]

    def plugin(self, steps, token=BROKER_TOKEN):
        payload = {
            "config": {"brokerUrl": f"http://127.0.0.1:{self.adapter_port}/internal/persona-tools/v1", "brokerToken": token},
            "pluginPath": str(self.plugin_path), "steps": steps,
        }
        driver = self.tmp / "driver.mjs"
        driver.write_text(DRIVER)
        done = subprocess.run(["node", str(driver)], input=json.dumps(payload), text=True, capture_output=True, timeout=120)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)


def step(agent, session, tool, params=None, run="run-1"):
    return {"agentId": agent, "sessionKey": f"agent:{agent}:openresponses-user:{session}", "runId": run,
            "tool": tool, "params": params or {}}


@pytest.fixture
def stack(layered, monkeypatch, tmp_path):  # noqa: F811
    api, (inst, per), tenant, client, base = layered
    import main as adapter
    from services.memory import main as memory_main

    monkeypatch.setenv(hs.LLM_LAYERS_ENV, "true")
    monkeypatch.setenv("PANTHEON_MEMORY_MENTAL_MODEL_MIN_REFRESH_SECONDS", "0")
    monkeypatch.setattr(hs, "_LAYERED_BANKS", set())
    alpha_private = per.create(persona(tenant_id=tenant, persona_id="persona-alpha", relevance_scope="persona_private",
                                       content={"summary": "ALPHA-PRIVATE 動能策略在流動性不足時應降低台灣股票曝險", "tags": ["family:momentum"]}))
    alpha_committee = per.create(persona(tenant_id=tenant, persona_id="persona-alpha", relevance_scope="persona_and_committee",
                                         source_event_id="pm-committee",
                                         content={"summary": "ALPHA-SHARED 動能策略可於會議分享的流動性教訓", "tags": ["family:momentum"]}))
    beta_private = per.create(persona(tenant_id=tenant, persona_id="persona-beta", relevance_scope="persona_private",
                                      content={"summary": "BETA-ONLY 動能策略的私人解讀 流動性", "tags": ["family:momentum"]}))
    fact = inst.create(institution(tenant_id=tenant))
    assert hs.flush_mirrors() and hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0

    mem_server, mem_thread, mem_port = _serve(memory_main.app)
    token = _make_jwt(tenant_id=tenant, roles=["operator", "consultation_session"])
    config = BrokerConfig(
        enabled=True, token=BROKER_TOKEN, memory_api_url=f"http://127.0.0.1:{mem_port}", memory_auth_token=token,
        memory_actor_id="memory-operator-1", memory_actor_roles=("operator",), registry_ttl_seconds=0.0,
        state_path=str(tmp_path / "broker.sqlite3"),
        limits={"persona_memory_recall": 4, "persona_mental_model_read": 4, "persona_proposal_submit": 3},
    )
    tools = persona_agent_runtime_policy()["tools"]
    registry = {"persona-alpha": {"id": "persona-alpha", "tools": tools}, "persona-beta": {"id": "persona-beta", "tools": tools},
                "main": {"id": "main"}}
    broker = PersonaToolBroker(config, agent_lookup=registry.get, memory_get=http_memory_get(config))
    monkeypatch.setattr(adapter, "_PERSONA_TOOL_BROKER_CONFIG", config)
    monkeypatch.setattr(adapter, "_PERSONA_TOOL_BROKER", broker)
    monkeypatch.setattr(adapter, "_ASSISTANT_SERVICE_TOKEN", SERVICE_TOKEN)
    ad_server, ad_thread, ad_port = _serve(adapter.app)
    ids = {"alpha_private": alpha_private.memory_id, "alpha_committee": alpha_committee.memory_id,
           "beta_private": beta_private.memory_id, "fact": fact.entry_id}
    try:
        yield Stack(adapter, broker, ad_port, tenant, ids, tmp_path), per, api
    finally:
        for server, thread in ((ad_server, ad_thread), (mem_server, mem_thread)):
            server.should_exit = True
            thread.join(timeout=5)


def _hit_ids(result):
    return [h["source_id"] for h in result["hits"]]


def test_daily_cycle_own_bank_facts_bank_refusals_and_per_run_record(stack):
    s, _, _ = stack
    reg = s.register("persona-alpha", "daily_cycle", "daily-1").json()
    run_ref = reg["run"]["run_ref"]
    q = {"query": "動能策略 流動性"}
    results = s.plugin([
        step("persona-alpha", "daily-1", "persona_memory_recall", q),                       # own bank
        step("persona-alpha", "daily-1", "persona_memory_recall", {**q, "bank": "facts"}),   # facts bank, separate call
        step("persona-alpha", "daily-1", "persona_memory_recall", {**q, "persona_id": "persona-beta"}),  # another persona's bank
        step("persona-alpha", "daily-1", "persona_memory_recall", {**q, "bank": "persona-beta"}),
        step("persona-alpha", "daily-1", "exec", {"cmd": "ls"}),                              # outside the profile (hook, known persona)
        step("main", "daily-1", "persona_memory_recall", q),                                 # unregistered (not a persona agent)
        step("persona-beta", "daily-1", "persona_memory_recall", q),                         # persona without a registered run
        step("persona-alpha", "other-session", "persona_memory_recall", q),                  # unregistered run
        step("persona-alpha", "daily-1", "persona_proposal_submit", {
            "kind": "no_action", "reason": "nothing to change", "evidence_refs": [], "case_file_item_ids": [],
            "none_applicable_reason": "no case file item applies"}),
    ])
    own, facts, other1, other2, exec_call, ghost, beta_run, no_run, proposal = results
    ids = s.ids
    own_ids = _hit_ids(own["details"])
    assert own["ok"] and ids["alpha_private"] in own_ids and ids["alpha_committee"] in own_ids
    assert ids["beta_private"] not in json.dumps(own) and "BETA-ONLY" not in json.dumps(own)
    assert own["details"]["bank"] == "own" and all(h["type"] == "persona" for h in own["details"]["hits"])
    assert facts["ok"] and ids["fact"] in _hit_ids(facts["details"]) and all(h["type"] == "institutional" for h in facts["details"]["hits"])
    for refused in (other1, other2):
        assert "invalid_parameters" in refused["error"] or "refused" in refused["error"]
    assert exec_call["blocked"] and "outside the persona tool profile" in exec_call["reason"]
    assert ghost["blocked"] and "not_a_persona_agent" in ghost["reason"]
    assert beta_run["blocked"] and "run_not_registered" in beta_run["reason"]
    assert no_run["blocked"] and "run_not_registered" in no_run["reason"]
    assert proposal["ok"] and proposal["details"] == {"recorded": True, "applied": False, "proposal_index": 0}

    record = s.record(run_ref)
    tools = [(c["tool"], c["outcome"]) for c in record["calls"]]
    assert tools == [("persona_memory_recall", "ok"), ("persona_memory_recall", "ok"), ("persona_memory_recall", "refused"),
                     ("persona_memory_recall", "refused"), ("persona_proposal_submit", "recorded")]
    assert record["persona_id"] == "persona-alpha" and record["purpose"] == "daily_cycle" and record["run_id"] == "run-1"
    assert ids["alpha_private"] in " ".join(record["calls"][0]["canonical_ids"])
    assert all(len(c["param_digest"]) == 64 for c in record["calls"])
    assert record["proposals"][0]["kind"] == "no_action"
    assert "BETA-ONLY" not in json.dumps(record)
    withheld = [e for e in s.broker.telemetry if e.get("reason") == "persona_scope_mismatch"]
    assert not withheld  # the real memory route never returned another persona's entry to filter

    # Every direct broker refusal is also available without the plugin.
    import httpx

    base = f"http://127.0.0.1:{s.adapter_port}/internal/persona-tools/v1/call"
    headers = {"Authorization": f"Bearer {BROKER_TOKEN}"}
    body = {"tool": "persona_reflect", "params": {"query": "q"}, "agent_id": "persona-alpha", "run_id": "run-1",
            "session_key": "agent:persona-alpha:openresponses-user:daily-1", "tool_call_id": "tc-direct"}
    refusal = httpx.post(base, json=body, headers=headers)
    assert refusal.status_code == 403 and refusal.json()["reason"] == "call_not_authorized_by_hook"
    assert httpx.post(base, json=body).status_code == 401
    assert s.plugin([step("persona-alpha", "daily-1", "persona_memory_recall", q)], token="wrong")[0]["blocked"]


def test_per_run_call_limit_with_real_memory(stack):
    s, _, _ = stack
    s.register("persona-alpha", "daily_cycle", "limit-1")
    calls = [step("persona-alpha", "limit-1", "persona_memory_recall", {"query": "流動性"}) for _ in range(5)]
    results = s.plugin(calls)
    assert [("ok" in r) for r in results] == [True, True, True, True, False]
    assert "run_call_limit_exceeded" in results[4]["error"]


def test_panel_interpretation_run_sees_committee_scope_and_facts_only(stack):
    s, _, _ = stack
    run_ref = s.register("persona-alpha", "panel_interpretation", "panel-1").json()["run"]["run_ref"]
    q = {"query": "動能策略 流動性"}
    own, facts, mental, proposal = s.plugin([
        step("persona-alpha", "panel-1", "persona_memory_recall", q),
        step("persona-alpha", "panel-1", "persona_memory_recall", {**q, "bank": "facts"}),
        step("persona-alpha", "panel-1", "persona_mental_model_read"),
        step("persona-alpha", "panel-1", "persona_proposal_submit", {
            "kind": "no_action", "reason": "r", "evidence_refs": [], "none_applicable_reason": "n"}),
    ])
    ids = s.ids
    assert own.get("ok"), own
    assert _hit_ids(own["details"]) == [ids["alpha_committee"]]
    assert "ALPHA-PRIVATE" not in json.dumps(own) and "BETA-ONLY" not in json.dumps(own)
    assert facts["ok"] and ids["fact"] in _hit_ids(facts["details"])
    for refused in (mental, proposal):
        assert refused["blocked"] and "tool_not_allowed_for_purpose" in refused["reason"]
    record = s.record(run_ref)
    assert [c["tool"] for c in record["calls"]] == ["persona_memory_recall"] * 2 + ["persona_mental_model_read", "persona_proposal_submit"]
    assert [c["outcome"] for c in record["calls"]] == ["ok", "ok", "refused", "refused"] and record["proposals"] == []


def test_mental_model_read_returns_only_the_callers_answers(stack):
    s, per, api = stack
    questions = [{"key": "family:momentum", "anchors": ["family:momentum"], "text": "動能策略有哪些教訓？"}]
    for persona_id in ("persona-alpha", "persona-beta"):
        declared = api.put("/api/memory/persona-standing-questions", headers=_svc(s.tenant),
                           json={"persona_id": persona_id, "questions": questions})
        assert declared.status_code == 200, declared.text
    s.register("persona-alpha", "daily_cycle", "mental-1")

    def answered():
        result = s.plugin([step("persona-alpha", "mental-1", "persona_mental_model_read")])[0]
        answers = (result.get("details") or {}).get("answers") or []
        return result if answers and answers[0].get("status") == "answered" else None

    result = _wait(answered)
    assert result, "no refreshed answer within the wait window"
    answer = result["details"]["answers"][0]
    assert answer["key"] == "family:momentum"
    assert answer["citations"] and set(answer["citations"]) <= {s.ids["alpha_private"], s.ids["alpha_committee"]}
    assert "BETA-ONLY" not in json.dumps(result) and s.ids["beta_private"] not in json.dumps(result)
    assert result["details"]["derived"] is True
    # A mental-model read cannot name another persona.
    refused = s.plugin([step("persona-alpha", "mental-1", "persona_mental_model_read", {"persona_id": "persona-beta"})])[0]
    assert "invalid_parameters" in refused["error"]
