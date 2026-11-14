"""Startup -> authenticated ensure -> live admission -> three-contribution memo.

All owner HTTP handlers are real. JSON/in-memory backing stores isolate tests
from PostgreSQL. Only Gateway transport/model output is faked; the CLI RPC
runner forwards to the same fake HTTP Gateway as the real Responses client.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from services.consultation.client import ConsultationServiceClient
from services.consultation.workflow_executor import run_tick
from services.consultation.workflow_state import WorkflowStateStore
from services.consultation.tests import test_persona_panel_integration as support
from services.consultation.tests.test_persona_panel_integration import stack
from services.control_plane.persona.test_panel_persona_bootstrap import load_persona_main
from services.persona import consult_triggers as triggers
from services.persona.write_owner import CreatePersonaRequest

ADAPTER = Path(__file__).resolve().parents[2] / "openclaw-gateway-adapter"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))
from assistant_openclaw_provider import AssistantOpenClawProvider
from consultation_provider import create_app, CONSULTATION_CONTRIBUTION_PATH, consult_agent_id


class Gateway(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), GatewayHandler)
        self.entries = [{"id": "main", "default": True, "tools": {"allow": ["*"]}},
                        {"id": support.INITIATOR, "tools": {"allow": ["trade"]}}]
        self.revision = 0
        self.calls = []
        self.ensure_failure = False
        self.tamper = False

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    def rpc_runner(self, args, **kwargs):
        method = args[3]
        params = json.loads(args[args.index("--params") + 1]) if "--params" in args else {}
        request = urllib.request.Request(self.url + "/rpc/" + method,
                                         data=json.dumps(params).encode(), method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return subprocess.CompletedProcess(args, 0, response.read().decode(), "")


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        gateway = self.server
        if self.path == "/rpc/config.get":
            if gateway.ensure_failure:
                self.send_error(503)
                return
            self.respond({"valid": True, "hash": str(gateway.revision),
                          "config": {"agents": {"list": copy.deepcopy(gateway.entries)}}})
        elif self.path == "/rpc/config.patch":
            assert body["baseHash"] == str(gateway.revision)
            for desired in json.loads(body["raw"])["agents"]["list"]:
                current = next((a for a in gateway.entries if a["id"] == desired["id"]), None)
                if current is None:
                    gateway.entries.append(desired)
                else:
                    current.update(desired)
            gateway.revision += 1
            self.respond({"ok": True})
        elif self.path == "/rpc/agents.list":
            self.respond({"agents": copy.deepcopy(gateway.entries)})
            # Simulate policy drift between ensure's admission and the
            # contribution endpoint's independent admission.
            if gateway.tamper:
                for entry in gateway.entries[2:]:
                    entry["tools"]["allow"] = ["trade"]
        elif self.path == "/v1/responses":
            envelope = json.loads(str(body["input"]).split("Consultation envelope JSON: ", 1)[1])
            request = envelope["request"]
            persona = request["participant"]["persona_id"]
            agent_id = self.headers["X-OpenClaw-Agent-Id"]
            assert agent_id == consult_agent_id(envelope["tenant_id"], persona)
            gateway.calls.append(persona)
            rid = envelope["request_id"]
            item = request["case_file"]["groups"]["facts"]["items"][0]["item_id"]
            contribution = {"contribution_id": f"contrib-{rid}-{persona}",
                "tenant_id": envelope["tenant_id"], "request_id": rid, "participant_type": "persona",
                "participant_ref": persona, "summary": f"{persona} reviews trial admission",
                "recommendation": "approve", "confidence": .8, "event_type": "persona_contribution",
                "event_content": {"claims": [{"claim": "Candidate meets review criteria", "evidence_refs": [item]}],
                                  "summary_evidence_refs": [item]},
                "evidence": [{"id": item, "evidence_type": "persona_review", "link": "artifact://trial"}],
                "findings": []}
            events = [{"type": "response.output_text.done", "text": json.dumps(contribution)},
                      {"type": "response.completed", "response": {"id": rid, "status": "completed"}}]
            raw = ("".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self.send_error(404)

    def respond(self, value):
        raw = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def provisioned_stack(stack, monkeypatch):
    from services.registry import service as registry_service
    from services.registry.storage import RegistryStore
    from services.registry.models import RegistryEntryCreate
    from services.memory.test_main import JWT_SECRET, _make_jwt

    monkeypatch.setenv("PANTHEON_OPENCLAW_AGENT_REGISTRY_LOCK", str(stack["root"] / "registry.lock"))
    monkeypatch.setenv("PERSONA_DEFAULT_TENANT_ID", support.TENANT)
    monkeypatch.setenv("PERSONA_DAILY_CYCLE_ENABLED", "false")
    monkeypatch.setenv("PERSONA_POOL_STORE_PATH", str(stack["root"] / "pools.json"))
    monkeypatch.setenv("PERSONA_STORE_PATH", str(stack["root"] / "startup-personas.json"))
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "control-plane" / "persona"))
    persona = load_persona_main()
    assert not persona.PERSONA_OWNER_API.state.persona_owner.list()
    owner = persona.PERSONA_OWNER_API.state.persona_owner
    owner.create(CreatePersonaRequest(actor_id="operator-bff", persona_id=support.INITIATOR,
        name="Trader", mandate="paper research", tenant_id=support.TENANT))
    registry = RegistryStore()
    registry.create(RegistryEntryCreate(artifact_type="strategy_spec", strategy_id="momentum", version="1.0.0",
        artifact_state="approved", checksum="sha256:candidate", evaluation_summary={"sharpe": 1.1}),
        "trial-candidate", actor={"tenant": support.TENANT})
    monkeypatch.setattr(registry_service, "get_store", lambda: registry)
    monkeypatch.setenv("PANTHEON_REGISTRY_STORE_BACKEND", "memory")
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", JWT_SECRET)
    token = _make_jwt(roles=["operator"], tenant_id=support.TENANT)
    monkeypatch.setenv("PANTHEON_REGISTRY_SERVICE_TOKEN", token)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", _make_jwt(
        roles=["memory-writer", "operator"], tenant_id=support.TENANT))
    monkeypatch.setenv("PANTHEON_MEMORY_ACTOR_ROLES", "operator")
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    gateway = Gateway()
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    try:
        with support._serve(persona.app) as persona_url, support._serve(registry_service.app) as registry_url:
            monkeypatch.setenv("PANTHEON_PERSONA_API_URL", persona_url)
            monkeypatch.setenv("PANTHEON_REGISTRY_API_URL", registry_url)
            monkeypatch.setenv("PERSONA_REGISTRY_BASE_URL", registry_url)
            monkeypatch.setenv("PERSONA_REGISTRY_SERVICE_TOKEN", token)
            monkeypatch.setenv("PANTHEON_MEMORY_API_URL", stack["memory"])
            assert persona.app.state.panel_personas["status"] == "ready"
            from persona_strategy_pool import AppendPoolVersionRequest
            persona._pool_store().append(support.INITIATOR, support.TENANT, AppendPoolVersionRequest(
                expected_version=0, change_reason="test fixture initial approved member", members=[{
                    "artifact_ref": "trial-candidate", "checksum": "sha256:candidate", "stage": "active",
                    "risk_weight": .1, "baseline_ref": "baseline", "joined_at": "2026-11-14T00:00:00Z"}]),
                actor_id="test")
            real_provider = AssistantOpenClawProvider(gateway_url=gateway.url, token="gateway-test-token",
                agent_id="main", _which_func=lambda _: "/test/openclaw", _run_func=gateway.rpc_runner)
            adapter = create_app(provider=real_provider, provider_token=support.PROVIDER_TOKEN,
                replay_path=stack["root"] / "provider.sqlite")
            with support._serve(adapter) as adapter_url:
                cfg = support._config(stack, adapter_url + CONSULTATION_CONTRIBUTION_PATH)
                yield stack, cfg, gateway, persona
    finally:
        gateway.shutdown()
        gateway.server_close()
        thread.join(timeout=5)


def open_trial(stack):
    requirement = triggers.required_consultation(triggers.DECISION_TRIAL_ADMISSION,
        "trial-candidate", initiator_persona_id=support.INITIATOR, touches=["paper_canary_candidacy"])
    client = ConsultationServiceClient(base_url=stack["api"], token=support.SERVICE_TOKEN, tenant_id=support.TENANT)
    return client.open_persona_panel(requirement, persona_id=support.INITIATOR, trace_id="trace-provision")


def test_missing_personas_and_agents_reach_three_contribution_memo(provisioned_stack):
    stack, cfg, gateway, persona = provisioned_stack
    assert len(persona.PERSONA_OWNER_API.state.persona_owner.list()) == 3
    assert len(gateway.entries) == 2
    before = copy.deepcopy(gateway.entries)
    request = open_trial(stack)
    result = run_tick(config=cfg, state=WorkflowStateStore(cfg.state_path))
    assert result["completed"] == 1, json.dumps(result)
    assert sorted(gateway.calls) == sorted([support.INITIATOR, triggers.RISK_PERSONA_ID, triggers.RED_TEAM_PERSONA_ID])
    assert len(gateway.entries) == 5 and gateway.entries[:2] == before
    memos = support._get(stack["api"], "/api/consult/memos?request_id=" + request["request_id"])
    assert len(memos) == 1 and memos[0]["status"] == "published"
    assert gateway.revision == 3
    support._provider(cfg).ensure_agents(tenant_id=support.TENANT,
        persona_ids=[support.INITIATOR, triggers.RISK_PERSONA_ID, triggers.RED_TEAM_PERSONA_ID])
    assert gateway.revision == 3


@pytest.mark.parametrize("failure", ["ensure", "tools"])
def test_failure_blocks_without_model_calls_then_retries(provisioned_stack, failure):
    stack, cfg, gateway, persona = provisioned_stack
    request = open_trial(stack)
    from dataclasses import replace
    cfg = replace(cfg, batch_size=1)
    state = WorkflowStateStore(cfg.state_path)
    gateway.ensure_failure = failure == "ensure"
    gateway.tamper = failure == "tools"
    result = run_tick(config=cfg, state=state)
    assert result["completed"] == 0 and result["blocked"] > 0, json.dumps(result)
    assert not gateway.calls
    assert not support._get(stack["api"], "/api/consult/memos?request_id=" + request["request_id"])
    gateway.ensure_failure = gateway.tamper = False
    result = run_tick(config=cfg, state=state)
    assert result["completed"] == 1, json.dumps(result)
