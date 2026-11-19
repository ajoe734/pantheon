"""Pre-meeting interpretation turns through the real consultation workflow.

The workflow executor, the adapter's consultation route, the persona tool broker and
memory-svc all run for real. Only the OpenClaw gateway is replaced by a double that
plays each persona agent: it calls the broker exactly as the gateway plugin does
(agent id, run id and session key supplied by the host, never by the model).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

_ADAPTER_DIR = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

import consultation_provider as cp  # noqa: E402
from assistant_openclaw_provider import derive_session_user  # noqa: E402
from persona_tool_broker import BrokerConfig, PersonaToolBroker, http_memory_get  # noqa: E402
from integrations.openclaw.persona_agent_sync import persona_agent_runtime_policy  # noqa: E402

from services.consultation import workflow_executor as wf  # noqa: E402
from services.consultation.client import ConsultationServiceClient  # noqa: E402
from services.consultation.tests.test_panel_case_file import _get  # noqa: E402
from services.consultation.tests.test_persona_own_agent_routing import DENY_ALL  # noqa: E402
from services.consultation.tests.test_persona_panel_integration import (  # noqa: E402
    INITIATOR, PROVIDER_TOKEN, SERVICE_TOKEN, TENANT, _config, _provider, _serve, stack,  # noqa: F401
    triggers,
)
from services.consultation.workflow_state import WorkflowStateStore  # noqa: E402
from services.memory.test_main import _make_jwt, _persona_auth_payload  # noqa: E402

BROKER_TOKEN = "broker-secret"
LONG_TAIL = " ".join(f"detail-{i}" for i in range(400))  # far beyond any prompt-trim size
PRIVATE = "PRIVATE-INTERPRETATION-SENTINEL"
SHARED = "momentum 2330 trial admission liquidity SHARED-NOTE"


class GatewayDouble:
    """The gateway: persona agents (own tools via broker) and deny-all consult agents."""

    def __init__(self, broker: PersonaToolBroker, personas: list[str], behaviour: str) -> None:
        self.broker, self.personas, self.behaviour = broker, personas, behaviour
        self.panel_prompts: list[str] = []
        self.panel_agents: list[str] = []
        self.interpretations: dict[str, dict[str, Any]] = {}
        self.recalled: dict[str, list[dict[str, Any]]] = {}

    def _gateway_call(self, method, **kwargs):
        assert method == "config.get"
        return {"valid": True, "config": {"agents": {"list": [
            {"id": cp.consult_agent_id(TENANT, p), **DENY_ALL} for p in self.personas]}}}

    def gateway_agents_list(self, **kwargs):
        return [{"id": cp.consult_agent_id(TENANT, p)} for p in self.personas]

    def _recall(self, persona: str, session_user: str) -> list[dict[str, Any]]:
        body = {"tool": "persona_memory_recall", "agent_id": persona, "run_id": "run-" + persona,
                "session_key": f"agent:{persona}:openai-user:{session_user}", "tool_call_id": "tc-" + persona,
                "params": {"query": "momentum 2330 trial admission liquidity", "bank": "own", "limit": 10}}
        # As the plugin does: the before_tool_call hook authorizes with the host tool_call_id, then the tool
        # calls with the same agent, session and tool_call_id.
        authorized = self.broker.handle(authorization="Bearer " + BROKER_TOKEN, body=body, authorize_only=True)
        assert authorized.status_code == 200, authorized.body
        response = self.broker.handle(authorization="Bearer " + BROKER_TOKEN, body=body)
        assert response.status_code == 200, response.body
        return response.body["result"]["hits"]

    def invoke(self, prompt, **kwargs):
        agent_id = kwargs["agent_id"]
        if prompt.startswith("You are persona ") and "Before a committee meeting" in prompt:
            persona = agent_id
            assert persona in self.personas  # the persona's own agent, not a consult- agent
            if self.behaviour == "turn_fails":
                raise cp.OpenClawProviderError("gateway timeout", status_code=504, error_code="TIMEOUT")
            user = derive_session_user(operator_id=kwargs["operator_id"], session_id=kwargs["session_id"])
            hits = self._recall(persona, user)
            self.recalled[persona] = hits
            ids = [h["canonical_ref"] for h in hits]
            cited = ids + (["pm-stranger-0000"] if self.behaviour == "stranger_citation" else [])
            text = f"Interpretation by {persona}: " + LONG_TAIL
            self.interpretations[persona] = {"text": text, "citations": cited}
            return SimpleNamespace(status="completed", output={
                "agent_id": persona,
                "json_events": [{"item": {"text": json.dumps({"interpretation": text, "citations": cited})}}]})
        # The meeting itself: only a deny-all consult agent, never a tool call.
        self.panel_agents.append(agent_id)
        self.panel_prompts.append(prompt)
        envelope = json.loads(prompt.split("Consultation envelope JSON: ", 1)[1])
        request = envelope["request"]
        persona = request["participant"]["persona_id"]
        item = request["case_file"]["groups"]["facts"]["items"][0]["item_id"]
        answer = {
            "contribution_id": "gw-" + persona, "tenant_id": TENANT, "request_id": envelope["request_id"],
            "participant_type": "persona", "participant_ref": persona, "author_type": "persona",
            "summary": persona + " answer", "recommendation": "approve", "confidence": 0.7,
            "event_type": "persona_contribution",
            "event_content": {"claims": [{"claim": "claim", "evidence_refs": [item]}],
                              "summary_evidence_refs": [item]},
            "evidence": [{"id": item, "evidence_type": "case_file_item", "link": "casefile://item"}],
            "findings": []}
        return SimpleNamespace(status="completed", output={
            "agent_id": agent_id, "json_events": [{"item": {"text": json.dumps(answer)}}]})


def _prompt_for(gw: GatewayDouble, persona: str) -> str:
    return next(p for p in gw.panel_prompts
                if json.loads(p.split("Consultation envelope JSON: ", 1)[1])["request"]["participant"]["persona_id"] == persona)


def _seed(memory_url: str, persona: str) -> None:
    token = _make_jwt(tenant_id=TENANT, roles=["operator"])
    for scope, text in (("persona_and_committee", f"{SHARED} {persona}"),
                        ("persona_private", f"{PRIVATE} momentum 2330 trial admission liquidity {persona}")):
        r = httpx.post(memory_url + "/api/memory/persona-entries", headers={"Authorization": "Bearer " + token},
                       json=_persona_auth_payload(persona_id=persona, tenant_id=TENANT, relevance_scope=scope,
                                                  content={"summary": text}))
        assert r.status_code == 201, r.text


def _run(stack, monkeypatch, behaviour: str):
    requirement = triggers.required_consultation("trial_admission", "reg-trial", initiator_persona_id=INITIATOR)
    personas = [p.persona_id for p in requirement.participants]
    for persona in personas:
        _seed(stack["memory"], persona)
    operator = _make_jwt(tenant_id=TENANT, roles=["operator", "consultation_session"])
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv("PANTHEON_MEMORY_API_URL", stack["memory"])
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", operator)
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ID", "memory-operator-1")
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ROLES", "operator")
    monkeypatch.setattr(cp, "ensure_consult_agent",
                        lambda provider, tenant_id, persona_id: {"status": "ready", "tenant_id": tenant_id,
                                                                 "persona_id": persona_id})
    broker_config = BrokerConfig(
        enabled=True, token=BROKER_TOKEN, memory_api_url=stack["memory"], memory_auth_token=operator,
        memory_actor_id="memory-operator-1", memory_actor_roles=("operator",), registry_ttl_seconds=0.0,
        state_path=str(stack["root"] / "broker.sqlite3"))
    tools = persona_agent_runtime_policy()["tools"]
    broker = PersonaToolBroker(broker_config, agent_lookup={p: {"id": p, "tools": tools} for p in personas}.get,
                               memory_get=http_memory_get(broker_config))
    gateway = GatewayDouble(broker, personas, behaviour)
    adapter = cp.create_app(provider=gateway, provider_token=PROVIDER_TOKEN, expected_service_actor=wf.CONSUMER_NAME,
                            replay_path=stack["root"] / "adapter-replay.sqlite3", interpretation_broker=broker)
    with _serve(adapter) as url:
        config = _config(stack, url + cp.CONSULTATION_CONTRIBUTION_PATH)
        created = ConsultationServiceClient(base_url=stack["api"], token=SERVICE_TOKEN, tenant_id=TENANT
                                            ).open_persona_panel(requirement, persona_id=INITIATOR, trace_id="trace-int")
        result = wf.run_tick(config=config, state=WorkflowStateStore(config.state_path), provider=_provider(config))
    transcript = _get(stack["api"], f"/api/consult/requests/{created['request_id']}/transcript")
    records = {e["actor"]["actor_id"]: e["content"]["interpretation"]
               for e in transcript["events"] if e["event_type"] == "persona_contribution"}
    return result, gateway, broker, records, personas


def test_interpretation_mode_passes_full_text_with_checked_citations(stack, monkeypatch):
    result, gw, broker, records, personas = _run(stack, monkeypatch, "ok")
    assert result["completed"] == 1, json.dumps(result, default=str)
    assert set(records) == set(personas)  # one record per participant, stored in the meeting record
    for persona in personas:
        record = records[persona]
        assert record["mode"] == "interpretation"
        assert record["text"] == gw.interpretations[persona]["text"] and LONG_TAIL in record["text"]  # not truncated
        assert record["citations"] == gw.interpretations[persona]["citations"] and 0 < len(record["citations"]) <= 12
        prompt = _prompt_for(gw, persona)
        assert LONG_TAIL in prompt  # full interpretation reached the meeting
        for ref in record["citations"]:
            assert ref in prompt
        run = broker.run_record(record["run_ref"])
        assert run["purpose"] == "panel_interpretation"
        assert {i for c in run["calls"] for i in c["canonical_ids"]} >= set(record["citations"])
    # Meeting participants stayed on deny-all consult agents; private entries never entered anything.
    assert all(a.startswith(cp.CONSULT_AGENT_PREFIX) for a in gw.panel_agents)
    assert all(PRIVATE not in p for p in gw.panel_prompts)
    assert all(PRIVATE not in json.dumps(h) for hits in gw.recalled.values() for h in hits)
    assert all(PRIVATE not in json.dumps(r) for r in records.values())


@pytest.mark.parametrize("behaviour,reason", [
    ("turn_fails", "interpretation_turn_failed"),
    ("stranger_citation", "interpretation_citation_not_returned"),
])
def test_fallback_mode_passes_committee_entries_in_full_and_states_mode(stack, monkeypatch, behaviour, reason):
    result, gw, broker, records, personas = _run(stack, monkeypatch, behaviour)
    assert result["completed"] == 1, result
    for persona in personas:
        record = records[persona]
        assert record["mode"] == "fallback" and record["reason"].startswith(reason)
        assert "text" not in record  # a rejected or failed interpretation is never passed on
        prompt = _prompt_for(gw, persona)
        assert f"SHARED-NOTE {persona}" in prompt  # committee-visible entry in full
        assert PRIVATE not in prompt and LONG_TAIL not in prompt
        assert record["citations"]  # the shared entries' canonical ids
    if behaviour == "stranger_citation":
        assert all(r["rejected_citations"] == ["pm-stranger-0000"] for r in records.values())


def test_private_memory_in_case_file_is_still_refused(stack, monkeypatch):
    """The existing refusal stands: no interpretation turn runs for a case file carrying private memory."""
    requirement = triggers.required_consultation("trial_admission", "reg-trial", initiator_persona_id=INITIATOR)
    real = wf._compose_panel_case_file

    def poisoned(config, request, decision):
        case = real(config, request, decision)
        item = case["groups"]["facts"]["items"][0]
        item["memory_layer"] = "persona_private"
        body = {k: v for k, v in case.items() if k not in {"case_file_id", "digest"}}
        import hashlib
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=False, default=str).encode()).hexdigest()
        return {"case_file_id": f"casefile-{digest[:24]}", "digest": digest, **body}

    monkeypatch.setattr(wf, "_compose_panel_case_file", poisoned)
    result, gw, broker, records, personas = _run(stack, monkeypatch, "ok")
    assert result["completed"] == 0 and not gw.panel_prompts and not gw.interpretations
    assert "private memory cannot enter a panel case file" in json.dumps(result)
