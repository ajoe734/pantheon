"""Harness loop callers against real owners.

Real: persona owner + harness routes/state, shadow evaluator, adoption, packet store, the
adapter's replay-agent provisioning and policy route (served over HTTP), the Consultation service
and Memory/Governance/Evolution applications (the ``stack`` fixture). Scripted: persona model
answers and the OpenClaw gateway config store. Not hosted-performance evidence.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.consultation.tests.test_persona_panel_integration import (  # noqa: F401
    stack, _config, _provider, _provider_fake, _serve,
)
from services.persona.cron_ooda_closure import append_ooda_packet
from services.persona.harness import PersistentHarnessOwner
from services.persona.harness_cycle import HarnessLoop, attach_harness_observation, production_evidence_verifier
from services.persona.persona_cycle_controller import read_packets
from services.persona.test_harness import ROOT, headers, initialize, system  # noqa: F401
from services.persona.test_harness_shadow_eval import BOOK, remove, turn
from services.control_plane.ooda.ooda_loop_packet import OodaLoopPacket

ADAPTER = Path(__file__).resolve().parents[1] / "openclaw-gateway-adapter"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))


def adapter_main():
    name = "pantheon_adapter_main_for_loop"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ADAPTER / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Gateway:
    """In-memory gateway config store; the adapter's own provisioning code runs against it."""
    _agent_id = "main"

    def __init__(self):
        import json
        self.json = json
        self.entries = [{"id": "main", "default": True, "tools": {"allow": ["*"]}}]
        self.patches = 0

    def _gateway_call(self, method, params=None):
        if method == "config.get":
            return {"valid": True, "hash": str(self.patches), "config": {"agents": {"list": copy.deepcopy(self.entries)}}}
        assert method == "config.patch" and params["baseHash"] == str(self.patches)
        self.patches += 1
        for desired in self.json.loads(params["raw"])["agents"]["list"]:
            current = next((a for a in self.entries if a["id"] == desired["id"]), None)
            self.entries.append(desired) if current is None else current.update(desired)
        return {"ok": True}

    def gateway_agents_list(self):
        return copy.deepcopy(self.entries)


def terminal_packet(index, *, version=1, day=None, harness_observation=None, updated=None):
    stamp = updated or f"2026-11-0{index % 9 + 1}T01:00:00+00:00"
    packet = OodaLoopPacket(packet_id=f"frozen-{index:02d}", loop_type="paper_strategy", status="closed",
                            environment="paper", persona_ids=["p-alpha"], created_at=stamp, updated_at=stamp).to_dict()
    packet["tenant_id"] = "tenant-a"
    packet["persona_cycle"] = {"phase": "terminal", "failure_reason": None, "observe_bundle": {
        "persona_id": "p-alpha", "tenant_id": "tenant-a", "books": {"m1": BOOK}, "unavailable": [],
        "harness_version": version, "telemetry_refs": []}}
    return packet


def cycle_packet(evidence):
    change = {"cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.8}}
    proposal = {"kind": "harness_patch", "reason": "raise evidence bar", "evidence_refs": [],
                "parameters": {"patch_type": "threshold", "proposed_change": change,
                               "expected_effect": "fewer weak removals", "evidence_refs": [evidence]}}
    return {"packet_id": "ooda-cycle-1", "persona_cycle": {
        "output": {"proposals": [proposal]},
        "decisions": [{"kind": "harness_patch", "status": "requires_owner_gate"}]}}


PERSONA = {"persona_id": "p-alpha", "tenant_id": "tenant-a", "lifecycle_state": "paper_owner"}


@pytest.fixture
def env(system, stack, tmp_path, monkeypatch):
    client, owner, _, path, _, evidence = system
    harness = PersistentHarnessOwner(owner)  # production verifier, adoption, consultation, sender
    clock = [datetime.now(timezone.utc)]
    harness.adoption.now = lambda: clock[0]
    loop = HarnessLoop(harness, now=lambda: clock[0])
    initialize((client, owner, harness, path, {}, evidence))
    packets = tmp_path / "packets.jsonl"
    monkeypatch.setenv("PANTHEON_BFF_OODA_PACKET_STORE", str(packets))
    for i in range(3):
        append_ooda_packet(packets, terminal_packet(i))
    return SimpleNamespace(harness=harness, loop=loop, clock=clock, packets=packets, evidence=evidence,
                           owner=owner, monkeypatch=monkeypatch, stack=stack)


def state(env):
    return env.harness.read("p-alpha")


def configure_replay(env, *, ensure=True):
    """Serve the real adapter app with an in-memory gateway; point the owner's env at its probe."""
    from fastapi.testclient import TestClient  # noqa: F401
    env.monkeypatch.syspath_prepend(str(ADAPTER))
    main = adapter_main()
    gateway = Gateway()
    env.monkeypatch.setattr(main, "_OPENCLAW_AGENT_PROVIDER", gateway)
    env.monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(env.packets.parent / "registry.lock"))
    server = _serve(main.app)
    base = server.__enter__()
    env.monkeypatch.setattr(env, "_server", server, raising=False)
    if ensure:
        import urllib.request
        urllib.request.urlopen(urllib.request.Request(base + "/api/openclaw-adapter/shadow-replay/agent/ensure",
                                                      method="POST"), timeout=10).read()
    env.monkeypatch.setenv("PANTHEON_SHADOW_REPLAY_AGENT_ID", "shadow-replay-deny-all")
    env.monkeypatch.setenv("PANTHEON_SHADOW_REPLAY_POLICY_URL", base + "/api/openclaw-adapter/shadow-replay/policy")

    def replay_turn(agent_id, prompt):
        assert agent_id == "shadow-replay-deny-all" and "deny-all" in prompt
        return turn([remove(3, 0.9)] if '"version": "candidate"' in prompt else [remove(1, 0.2)])
    env.monkeypatch.setattr("integrations.openclaw.persona_ooda_runtime.run_persona_ooda_turn", replay_turn)
    return gateway, server


def test_unconfigured_replay_is_a_degraded_cycle_record_never_a_silent_keep_base(env):
    record = env.loop.run(PERSONA, cycle_packet(env.evidence))
    patch_id = record["actions"][0]["patch_id"]
    assert record["status"] == "degraded" and "shadow_provider_unavailable" in record["reasons"]
    row = state(env)["patches"][patch_id]
    assert row["status"] == "previewed"
    assert row["comparisons"][-1]["status"] == "incomplete" and "provider_unavailable" in row["comparisons"][-1]["reasons"]
    assert state(env)["active_version"] == 1 and state(env)["active_experiment"] is None


def test_unverifiable_replay_agent_is_degraded_not_adopted(env):
    configure_replay(env, ensure=False)  # the adapter never ensured the deny-all agent: probe fails closed
    record = env.loop.run(PERSONA, cycle_packet(env.evidence))
    assert record["status"] == "degraded" and "shadow_deny_all_provider_unverified" in record["reasons"]
    assert state(env)["active_version"] == 1


def test_cycle_patch_with_unresolvable_evidence_is_refused_with_reason(env):
    packet = cycle_packet({"kind": "packet", "ref": "no-such-packet", "moment": "no-such-packet"})
    record = env.loop.run(PERSONA, packet)
    assert record["status"] == "degraded"
    assert any("trajectory_evidence_unresolved" in r for r in record["reasons"])
    assert packet["persona_cycle"]["decisions"][0]["status"] == "patch_recorded"


def test_propose_compare_consult_adopt_observe_through_real_owners(env, monkeypatch):
    from dataclasses import replace
    from fastapi import FastAPI, Header
    from services.consultation.client import ConsultationServiceClient
    from services.consultation.workflow_executor import run_tick
    from services.consultation.workflow_state import WorkflowStateStore
    from services.evolution import main as evolution
    from services.memory.test_main import _make_jwt
    from services.persona.consult_triggers import RISK_PERSONA_ID
    stack_ = env.stack
    monkeypatch.setattr(evolution, "store", evolution.EvolutionDecisionStore(storage_path=str(stack_["root"] / "e.json")))
    roles = ["memory-writer", "consultation_session"]
    for key, value in {"CONSULTATION_HANDOFF_ALLOWED_TENANTS": "tenant-a",
                       "PANTHEON_MEMORY_AUTH_TOKEN": _make_jwt(roles=roles, tenant_id="tenant-a"),
                       "PANTHEON_MEMORY_ACTOR_ROLES": ",".join(roles), "PANTHEON_MEMORY_API_URL": stack_["memory"],
                       "PANTHEON_GOVERNANCE_API_URL": stack_["governance"], "PANTHEON_CONSULTATION_API_URL": stack_["api"],
                       "PANTHEON_EVOLUTION_API_URL": stack_["evolution"], "EVOLUTION_AUTH_MODE": "token",
                       "EVOLUTION_AUTH_TOKEN": "test-evolution-owner-token", "EVOLUTION_AUTH_ALLOWED_TENANTS": "tenant-a"}.items():
        monkeypatch.setenv(key, value)
    gateway, server = configure_replay(env)
    try:
        # 1. daily cycle proposal -> patch record -> precheck -> shadow on the ensured deny-all agent -> consultation opened
        packet = cycle_packet(env.evidence)
        first = env.loop.run(PERSONA, packet)
        patch_id = packet["persona_cycle"]["decisions"][0]["patch_id"]
        assert packet["persona_cycle"]["decisions"][0]["status"] == "patch_recorded"
        row = state(env)["patches"][patch_id]
        comparison = row["comparisons"][-1]
        assert (comparison["status"], comparison["decision"]) == ("complete", "CANDIDATE_BETTER")
        assert comparison["replay"]["deny_all_verified"] is True
        assert comparison["replay"]["replay_agent_id"] == "shadow-replay-deny-all"
        step = next(a for a in first["actions"] if a["step"] == "consultation")
        assert step["state"] == "opened" and first["status"] == "ok", first
        assert state(env)["active_version"] == 1
        # 2. before the memo exists the loop only waits
        waiting = env.loop.run(PERSONA, {"packet_id": "ooda-cycle-2", "persona_cycle": {}})
        assert next(a for a in waiting["actions"] if a["step"] == "consultation")["state"] == "pending"
        assert state(env)["active_version"] == 1
        # 3. the real panel publishes the memo
        facts = FastAPI()

        @facts.get("/api/registry/entries/{ref}")
        def registry(ref: str, tenant: str = Header(alias="X-Tenant-Id")):
            return {"registry_id": ref, "tenant_id": tenant, "strategy_id": "momentum", "artifact_type": "strategy_spec",
                    "version": "1", "artifact_state": "candidate",
                    "metadata": {"persona_id": "p-alpha", "underlyings": ["2330"], "persona_capital_binding_id": "pcb-alpha"},
                    "lineage": {"source_run_ids": ["bt-alpha"]}}

        @facts.get("/api/registry/strategies/{family}/entries")
        def history(family: str):
            return []

        @facts.get("/api/research-orchestrator/backtest-runs/{run}/owner-binding")
        def backtest(run: str):
            return {"status": "done", "gate_verdict": {"passed": True}, "evaluation_digest": "fixture-backtest"}

        @facts.get("/api/telemetry/runtime-summaries")
        def telemetry(tenant: str = Header(alias="X-Tenant-Id")):
            return {"summaries": [{"tenant_id": tenant, "persona_capital_binding_id": "pcb-alpha", "deployment_stage": "paper",
                                   "member_virtual_books": [{"member_id": "reg-member", "net_of_costs": {"pnl": 12}}]}]}

        @facts.post("/api/search/v2/query")
        def search(body: dict):
            return {"retrieval_mode": "keyword", "results": [{"result_id": "fixture-note", "relevance_score": 0.7,
                    "citations": ["doc-alpha"], "matched_items": [{"object_id": "doc-alpha", "excerpt": "momentum 2330"}]}]}

        with _serve(facts) as facts_url, _provider_fake({"p-alpha": "approve", RISK_PERSONA_ID: "approve"}) as provider:
            for service in ("REGISTRY", "RESEARCH", "TELEMETRY", "SEARCH"):
                monkeypatch.setenv(f"PANTHEON_{service}_API_URL", facts_url)
            cfg = replace(_config(stack_, f"http://127.0.0.1:{provider.server_address[1]}/contribute"), tenant_id="tenant-a")
            assert run_tick(config=cfg, state=WorkflowStateStore(cfg.state_path), provider=_provider(cfg))["completed"] == 1
        # 4. memo published -> adopted only with that memo
        adopted = env.loop.run(PERSONA, {"packet_id": "ooda-cycle-3", "persona_cycle": {}})
        assert any(a["step"] == "adopted" and a["version"] == 2 for a in adopted["actions"]), adopted
        committed = state(env)
        assert committed["active_version"] == 2 and committed["active_experiment"] == patch_id
        assert committed["patches"][patch_id]["adoption_ref"].startswith("memo-") or committed["patches"][patch_id]["adoption_ref"]
        # 5. ten trading-day packets carry the observation facts the observe step needs
        persona_row = env.owner.get("p-alpha").model_dump(mode="json")
        base_day = env.clock[0]
        for day in range(1, 11):
            env.clock[0] = base_day + timedelta(days=day)
            bundle_packet = terminal_packet(100 + day, version=2, updated=datetime.now(timezone.utc).isoformat())
            bundle = bundle_packet["persona_cycle"]["observe_bundle"]
            attach_harness_observation(persona_row, bundle, read_packets(env.packets), bundle_packet["packet_id"], env.clock[0])
            assert bundle["harness_observation"]["patch_id"] == patch_id, bundle
            assert bundle["harness_observation"]["confounders"] == []
            append_ooda_packet(env.packets, bundle_packet)
            persona_row = env.owner.get("p-alpha").model_dump(mode="json")
        observed = env.loop.run(PERSONA, {"packet_id": "ooda-cycle-4", "persona_cycle": {}})
        step = next(a for a in observed["actions"] if a["step"] == "observed")
        assert step["outcome"] == "no_effect", observed
        final = state(env)
        assert final["active_experiment"] is None and final["patches"][patch_id]["status"] == "observed"
        outbox = final["outbox"]
        assert outbox and all(v["status"] == "delivered" for v in outbox.values()), [(v["kind"], v.get("error")) for v in outbox.values()]
    finally:
        server.__exit__(None, None, None)


def test_ranking_evidence_of_evaluator_drafts_resolves_in_production_verifier(env):
    ref = SimpleNamespace(kind="packet", ref="ev-1", moment="snapshot-1")
    persona = env.owner.get("p-alpha")
    rows = [{"persona_id": "p-alpha", "evidence_refs": [{"refId": "ev-1"}]}]
    verify = production_evidence_verifier(lambda p, r: False, ranking_reader=lambda p, moment: rows if moment == "snapshot-1" else [])
    assert verify(persona, ref) is True
    assert verify(persona, SimpleNamespace(kind="packet", ref="ev-1", moment="other")) is False
    assert verify(persona, SimpleNamespace(kind="packet", ref="ev-9", moment="snapshot-1")) is False
    unconfigured = production_evidence_verifier(lambda p, r: False)  # no ranking BFF configured: fail closed
    assert unconfigured(persona, ref) is False


def test_evaluator_tool_profile_draft_becomes_a_patch_record_with_resolving_ranking_evidence(env, system, monkeypatch):
    """The evaluator's own request builder -> the real owner route -> production verifier -> ranking read."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.persona.write_owner import create_app
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "persona-evaluator-agent"))
    import persona_evaluator_agent as pea
    client = TestClient(create_app(owner=env.owner, harness_owner=env.harness))
    bff = FastAPI()

    @bff.get("/bff/management/quarterly-ranking")
    def ranking():
        return {"data": {"ranking_snapshot_id": "snapshot-1", "items": [
            {"persona_id": "p-alpha", "evidence_refs": [{"refId": "ranking-ev-1"}]}]}, "page_info": {}}

    def fetch(url, data=None, headers=None, **_):  # the evaluator's transport, bound to the real owner routes
        path = url.split("/api/", 1)[1]
        response = client.get("/api/" + path, headers=headers) if data is None else client.post("/api/" + path, json=data, headers=headers)
        assert response.status_code < 300, response.text
        return response.json()

    persona_headers = headers()
    rec = {"persona_id": "p-alpha", "action_id": "increase_research_budget", "session_id": "session-1",
           "rationale": "ranking shows under-use of research tools", "ranking_snapshot_id": "snapshot-1",
           "recommendation_id": "rec-1", "evidence_ref_ids": ["ranking-ev-1"], "stage": "paper_running"}
    request = pea.route_paper_advice(rec, tenant="tenant-a", persona_url="http://owner", persona_token="x",
                                     fetch=lambda url, **kw: fetch(url, **{**kw, "headers": persona_headers}))
    created = client.post("/api/" + request["url"].split("/api/", 1)[1], json=request["body"], headers=persona_headers)
    assert created.status_code == 201, created.text
    patch_id = request["body"]["patch_id"]
    # Without the ranking read configured the evaluator's reference cannot resolve: refused, never accepted.
    refused = client.post(f"{ROOT}/patches/{patch_id}/precheck", headers=persona_headers)
    assert refused.status_code == 422 and "trajectory_evidence_unresolved" in refused.text
    with _serve(bff) as bff_url:
        monkeypatch.setenv("PANTHEON_RANKING_BFF_URL", bff_url)
        monkeypatch.setenv("PANTHEON_RANKING_BFF_TOKEN", "ranking-read-token")
        checked = client.post(f"{ROOT}/patches/{patch_id}/precheck", headers=persona_headers)
    assert checked.status_code == 200, checked.text
    assert checked.json()["status"] == "previewed"


@pytest.mark.parametrize("decision_status,params,expected", [
    ("meeting_opened", {"meeting_type": "MT-2"}, "meeting_opened"),       # answered by the meeting triggers
    ("meeting_refused", {"meeting_type": "MT-2"}, "meeting_refused"),
    ("requires_owner_gate", {"patch_id": "none"}, "requires_owner_gate"),  # gate-panel ask: loop behaviour unchanged
])
def test_meeting_consult_request_decision_is_not_overwritten_by_the_harness_loop(env, decision_status, params, expected):
    packet = {"packet_id": "ooda-cycle-2", "persona_cycle": {
        "output": {"proposals": [{"kind": "consult_request", "reason": "r", "evidence_refs": [], "parameters": params}]},
        "decisions": [{"kind": "consult_request", "status": decision_status, "reason": "meeting_reason"}]}}
    env.loop.run(PERSONA, packet)
    assert packet["persona_cycle"]["decisions"][0]["status"] == expected
