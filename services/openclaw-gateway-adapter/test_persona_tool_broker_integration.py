"""Persona tool path against real containers (design H9 / H10), opt-in.

Chain under test, every hop real HTTP: the compiled ``pantheon-persona-tools`` plugin (run by node, its
``before_tool_call`` hook then ``execute``) -> the real adapter app (broker routes) -> the real memory
service app (strict JWT) -> a real Hindsight + Postgres container pair (LLM layers on, model calls routed
through the real adapter memory-LLM bridge into the gateway double used by the memory tests).

Not real here: the OpenClaw gateway process that would load the plugin and the agent registry the broker
reads (``agents.list``) - both are replaced by the plugin's own hook/execute contract and a registry
fixture built from the governed ``persona_agent_runtime_policy()``. The gateway-image build and plugin
load is covered separately in ``docs/deployment/evidence/OPENCLAW-PERSONA-TOOL-BROKER-R2-20261114``.

Opt in with ``PANTHEON_RUN_HINDSIGHT_CONTAINER_PROBE=1`` (starts hindsight + postgres containers; the
images are built from deploy/hindsight and deploy/memory-index-postgres, see test_hindsight_integration).
"""
from __future__ import annotations

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

from integrations.openclaw.persona_agent_sync import persona_agent_runtime_policy
from persona_tool_broker import BrokerConfig, PersonaToolBroker, http_memory_get
from services.memory import hindsight_index as hs
from services.memory.test_hindsight_integration import (  # noqa: F401  (fixtures)
    _svc, _wait, layered, layered_stack,
)
from services.memory.test_hindsight_index import institution, persona
from services.memory.test_main import JWT_SECRET, _make_jwt

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
            "session_key": "agent:persona-alpha:openresponses-user:daily-1"}
    refusal = httpx.post(base, json=body, headers=headers)
    assert refusal.status_code == 403 and refusal.json()["reason"] == "tool_outside_profile"
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
