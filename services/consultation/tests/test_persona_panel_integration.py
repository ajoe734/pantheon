"""Trigger -> three-persona panel -> memo -> memory, through real entry points.

Real: consultation-svc, its workflow executor, the Persona owner (bootstrap via
``POST /api/personas``), governance handoff intake, evolution health and the
memory service (Learn-feedback writeback). Faked: only the provider HTTP that
stands in for each persona's own agent.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest
import uvicorn

from services.consultation import main as consultation_main
from services.consultation.client import ConsultationServiceClient
from services.consultation.provider import HttpContributionProvider
from services.consultation.store import ConsultationStore
from services.consultation.workflow_executor import ExecutorConfig, run_tick
from services.consultation.workflow_state import WorkflowStateStore
from services.memory.test_main import JWT_SECRET, _make_jwt
from services.persona import consult_triggers as triggers

TENANT = "tenant-panel"
SERVICE_TOKEN = "panel-consult-service-token"
PROVIDER_TOKEN = "panel-provider-token"
HANDOFF_TOKEN = "panel-handoff-token"
PERSONA_TOKEN = "panel-persona-token"
INITIATOR = "persona-qinghe"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve(app: Any) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() >= deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class ProviderFake(ThreadingHTTPServer):
    """The only fake: each persona's own agent, reached over provider HTTP."""

    answers: dict[str, str]
    calls: list[dict[str, Any]]


class _ProviderHandler(BaseHTTPRequestHandler):
    server: ProviderFake

    def log_message(self, *_a: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        participant = payload["request"]["participant"]
        persona_id = participant["persona_id"]
        self.server.calls.append(
            {
                "persona_id": persona_id,
                "idempotency_key": self.headers.get("Idempotency-Key"),
                "payload": payload,
            }
        )
        rid = payload["request_id"]
        rec = self.server.answers[persona_id]
        body = {
            "status": "completed",
            "contribution": {
                "contribution_id": f"contrib-{rid}-{persona_id}",
                "tenant_id": payload["tenant_id"],
                "request_id": rid,
                "participant_type": "persona",
                "participant_ref": persona_id,
                "summary": f"{persona_id} answered {rec} from its own agent.",
                "recommendation": rec,
                "confidence": 0.7,
                "event_type": "persona_contribution",
                "event_content": {"claim": f"{persona_id} position: {rec}"},
                "evidence": [
                    {
                        "id": f"ev-{rid}-{persona_id}",
                        "evidence_type": "persona_review",
                        "link": f"artifact://{rid}/{persona_id}",
                    }
                ],
                "findings": [],
            },
        }
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextmanager
def _provider_fake(answers: dict[str, str]) -> Iterator[ProviderFake]:
    server = ProviderFake(("127.0.0.1", 0), _ProviderHandler)
    server.answers = answers
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("CONSULTATION_AUTH_REQUIRED", "true")
    monkeypatch.setenv("CONSULTATION_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", PERSONA_TOKEN)
    monkeypatch.setenv("PERSONA_STORE_BACKEND", "json")
    monkeypatch.setenv("PERSONA_STORE_PATH", str(tmp_path / "personas.json"))
    # Memory runs strict, exactly as deployed: the writer presents a real JWT.
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.delenv("PANTHEON_MEMORY_AUTHZ_MODE", raising=False)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_TOKEN", _make_jwt(roles=["memory-writer"], tenant_id=TENANT))
    monkeypatch.setenv("PANTHEON_MEMORY_STORE", str(tmp_path / "institutional.json"))
    monkeypatch.setenv("PANTHEON_PERSONA_MEMORY_STORE", str(tmp_path / "persona.json"))
    monkeypatch.setenv("PANTHEON_MEMORY_DATA_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("CONSULTATION_HANDOFF_TOKEN", HANDOFF_TOKEN)
    monkeypatch.setenv(
        "CONSULTATION_HANDOFF_ALLOWED_SERVICE_ACTOR", "consultation-workflow-executor"
    )
    monkeypatch.setenv("CONSULTATION_HANDOFF_ALLOWED_TENANTS", TENANT)
    consultation_main.store = ConsultationStore(str(tmp_path / "domain"))
    consultation_main.workflow_state = WorkflowStateStore(tmp_path / "wf.sqlite3")

    from services.governance import main as governance_main
    from services.governance.record_store import JsonGovernanceRecordStore
    from services.memory import main as memory_main
    from services.persona.write_owner import create_app as create_persona_app
    from services.evolution import main as evolution_main

    handoffs = JsonGovernanceRecordStore(
        tmp_path / "handoffs.json", id_fields=("handoff_id",)
    )
    monkeypatch.setattr(governance_main, "consultation_handoff_store", handoffs)
    with _serve(consultation_main.app) as api, _serve(memory_main.app) as memory, _serve(
        governance_main.app
    ) as governance, _serve(evolution_main.app) as evolution, _serve(
        create_persona_app()
    ) as persona:
        yield {
            "api": api,
            "memory": memory,
            "governance": governance,
            "evolution": evolution,
            "persona": persona,
            "root": tmp_path,
            "handoffs": handoffs,
            "memory_main": memory_main,
        }


def _config(stack: dict[str, Any], provider_url: str) -> ExecutorConfig:
    return ExecutorConfig(
        api_url=stack["api"],
        tenant_id=TENANT,
        api_token=SERVICE_TOKEN,
        provider_url=provider_url,
        provider_token=PROVIDER_TOKEN,
        provider_service_actor="consultation-workflow-executor",
        handoff_sink_url=stack["governance"] + "/api/governance/consultation-handoffs",
        handoff_token=HANDOFF_TOKEN,
        worker_id="panel-worker",
        state_path=str(stack["root"] / "wf.sqlite3"),
        memory_url=stack["memory"],
        evolution_url=stack["evolution"],
        retry_after_seconds=0,
        timeout_seconds=15.0,
        batch_size=5,
    )


def _provider(cfg: ExecutorConfig) -> HttpContributionProvider:
    return HttpContributionProvider(
        endpoint=cfg.provider_url,
        bearer_token=cfg.provider_token,
        service_actor=cfg.provider_service_actor,
        timeout_seconds=cfg.timeout_seconds,
    )


def _bootstrap(stack: dict[str, Any]) -> dict[str, str]:
    return triggers.bootstrap_consult_personas(
        persona_url=stack["persona"], token=PERSONA_TOKEN, tenant_id=TENANT
    )


def _run_panel(
    stack: dict[str, Any],
    answers: dict[str, str],
    *,
    decision_type: str,
    touches: tuple[str, ...],
    decision_id: str,
) -> tuple[dict[str, Any], dict[str, Any], ProviderFake]:
    with _provider_fake(answers) as fake:
        cfg = _config(stack, f"http://127.0.0.1:{fake.server_address[1]}/contribute")
        requirement = triggers.required_consultation(
            decision_type,
            decision_id,
            initiator_persona_id=INITIATOR,
            touches=touches,
        )
        assert requirement is not None
        client = ConsultationServiceClient(
            base_url=stack["api"], token=SERVICE_TOKEN, tenant_id=TENANT
        )
        created = client.open_persona_panel(
            requirement, persona_id=INITIATOR, trace_id=f"trace-{decision_id}"
        )
        result = run_tick(config=cfg, state=WorkflowStateStore(cfg.state_path), provider=_provider(cfg))
        return created, result, fake


def _memory_rows(stack: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    mm = stack["memory_main"]
    return (
        mm._persona_store().list_for_persona(INITIATOR)
        if hasattr(mm._persona_store(), "list_for_persona")
        else [],
        mm._store().list(),
    )


def test_trigger_to_three_persona_panel_to_memo_and_memory(stack: dict[str, Any]) -> None:
    created_personas = _bootstrap(stack)
    assert created_personas == {
        triggers.RISK_PERSONA_ID: "created",
        triggers.RED_TEAM_PERSONA_ID: "created",
    }
    # Idempotent: a second bootstrap creates nothing.
    assert set(_bootstrap(stack).values()) == {"exists"}

    answers = {
        INITIATOR: "approve",
        triggers.RISK_PERSONA_ID: "approve_with_conditions",
        triggers.RED_TEAM_PERSONA_ID: "approve_with_conditions",
    }
    created, result, fake = _run_panel(
        stack,
        answers,
        decision_type=triggers.DECISION_HARNESS_PATCH,
        touches=("guardrails",),
        decision_id="patch-1",
    )
    assert result["errors"] == [], result
    assert result["completed"] == 1, result
    request_id = created["request_id"]

    # Each persona was asked separately through its own agent; nobody saw an answer.
    assert sorted(c["persona_id"] for c in fake.calls) == sorted(answers)
    assert len({c["idempotency_key"] for c in fake.calls}) == 3
    for call in fake.calls:
        assert "recommendation" not in json.dumps(call["payload"]["request"]["participant"])
        assert "answered" not in json.dumps(call["payload"])

    api = stack["api"]
    memos = _get(api, "/api/consult/memos?request_id=" + request_id)
    assert len(memos) == 1 and memos[0]["status"] == "published"
    assert "no vote" in memos[0]["summary"].lower()
    assert "Agreements:" in memos[0]["summary"]
    transcript = _get(api, f"/api/consult/requests/{request_id}/transcript")
    actors = {e["actor"]["actor_id"] for e in transcript["events"]}
    assert set(answers) <= actors
    assert any(e["event_type"] == "panel_synthesis" for e in transcript["events"])
    by_target = _get(api, "/api/consult/targets/harness_patch/patch-1/memos")
    assert [m["memo_id"] for m in by_target] == [memos[0]["memo_id"]]

    # Memo linked to the gated decision in governance, acknowledged.
    handoff = _get(api, f"/api/consult/requests/{request_id}/handoffs")[0]
    assert handoff["status"] == "acknowledged"
    assert handoff["target_gate"] == "consultation.panel.harness_patch.reviewed"
    assert f"consultation://memos/{memos[0]['memo_id']}" in handoff["evidence_refs"]
    assert stack["handoffs"].get(handoff["handoff_id"])["memo_ids"] == [memos[0]["memo_id"]]

    # Initiator memory outcome + institutional cross-persona observation.
    mm = stack["memory_main"]
    persona_rows = mm._persona_store().find_by_source_event(
        source_event_type="consultation_closed",
        source_event_id=f"consultation:{request_id}",
        write_authority="consultation-svc",
        active_only=False,
    )
    assert [r.persona_id for r in persona_rows] == [INITIATOR]
    assert persona_rows[0].memory_type == "consultation_outcome"
    inst_rows = mm._store().find_by_source_event(
        source_event_type="consultation_closed",
        source_event_id=f"consultation:{request_id}",
        write_authority="consultation-svc",
        active_only=False,
    )
    assert len(inst_rows) == 1
    assert inst_rows[0].knowledge_type == "cross_persona_observation"


def test_risk_oppose_blocks_auto_adoption_and_becomes_operator_decision(
    stack: dict[str, Any],
) -> None:
    _bootstrap(stack)
    answers = {
        INITIATOR: "approve",
        triggers.RISK_PERSONA_ID: "reject",
    }
    created, result, _fake = _run_panel(
        stack,
        answers,
        decision_type=triggers.DECISION_TRIAL_ADMISSION,
        touches=(),
        decision_id="trial-1",
    )
    assert result["completed"] == 1, result
    request_id = created["request_id"]
    memo = _get(stack["api"], "/api/consult/memos?request_id=" + request_id)[0]
    assert memo["recommendation"] == "escalate"
    assert any(f["category"] == "risk_veto" for f in memo["findings"])
    transcript = _get(stack["api"], f"/api/consult/requests/{request_id}/transcript")
    gate = next(e for e in transcript["events"] if e["event_type"] == "panel_synthesis")["content"]["gate"]
    assert gate["outcome"] == "operator_decision"
    assert gate["operator_decision_required"] is True
    assert gate["vetoed_by"] == triggers.RISK_PERSONA_ID


def _adopt(stack: dict[str, Any], decision_type: str, decision_id: str) -> tuple[int, Any]:
    req = urllib.request.Request(
        stack["governance"] + "/api/governance/consultation-gates/adopt",
        data=json.dumps({"type": decision_type, "id": decision_id}).encode(),
        headers={
            "Authorization": f"Bearer {HANDOFF_TOKEN}",
            "X-Pantheon-Tenant-Id": TENANT,
            "X-Pantheon-Service-Actor": "consultation-workflow-executor",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_risk_veto_refuses_real_auto_adoption_at_decision_owner(
    stack: dict[str, Any],
) -> None:
    _bootstrap(stack)
    _created, result, _fake = _run_panel(
        stack,
        {INITIATOR: "approve", triggers.RISK_PERSONA_ID: "reject"},
        decision_type=triggers.DECISION_TRIAL_ADMISSION,
        touches=(),
        decision_id="trial-veto",
    )
    assert result["completed"] == 1, result
    stored = stack["handoffs"].list_all()[0]
    assert stored["gated_decision"] == {"type": "trial_admission", "id": "trial-veto"}
    assert stored["operator_decision"]["status"] == "pending"
    status, body = _adopt(stack, "trial_admission", "trial-veto")
    assert status == 409
    assert body["detail"]["operator_decision_required"] is True
    assert body["detail"]["adopted"] is False
    # An unreviewed decision is not adoptable either.
    assert _adopt(stack, "trial_admission", "never-reviewed")[0] == 409


def test_risk_initiated_patch_keeps_veto_end_to_end(stack: dict[str, Any]) -> None:
    _bootstrap(stack)
    rid = triggers.RISK_PERSONA_ID
    with _provider_fake({rid: "reject", triggers.RED_TEAM_PERSONA_ID: "approve"}) as fake:
        cfg = _config(stack, f"http://127.0.0.1:{fake.server_address[1]}/contribute")
        requirement = triggers.required_consultation(
            triggers.DECISION_HARNESS_PATCH,
            "patch-risk-init",
            initiator_persona_id=rid,
            touches=("guardrails",),
        )
        assert requirement is not None
        assert [p for p in requirement.participants if p.persona_id == rid][0].may_veto
        client = ConsultationServiceClient(
            base_url=stack["api"], token=SERVICE_TOKEN, tenant_id=TENANT
        )
        client.open_persona_panel(requirement, persona_id=rid, trace_id="trace-ri")
        result = run_tick(
            config=cfg, state=WorkflowStateStore(cfg.state_path), provider=_provider(cfg)
        )
    assert result["completed"] == 1, result
    assert stack["handoffs"].list_all()[0]["gate"]["operator_decision_required"] is True
    assert _adopt(stack, "harness_patch", "patch-risk-init")[0] == 409


def test_panel_decision_must_match_request_target(stack: dict[str, Any]) -> None:
    requirement = triggers.required_consultation(
        triggers.DECISION_TRIAL_ADMISSION, "trial-x", initiator_persona_id=INITIATOR
    )
    assert requirement is not None
    body = {
        "request_type": "persona_policy",
        "requested_by": {"actor_type": "persona", "actor_id": INITIATOR},
        "from_persona_id": INITIATOR,
        "target_type": "envelope_publication",
        "target_id": "env-other",
        "metadata": {"panel": requirement.to_panel_metadata()},
        "trace_id": "trace-mm",
    }
    req = urllib.request.Request(
        stack["api"] + "/api/consult/requests",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {SERVICE_TOKEN}",
            "X-Pantheon-Tenant-Id": TENANT,
            "X-Pantheon-Service-Actor": f"persona:{INITIATOR}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 422


def test_panel_open_requires_persona_service_caller_and_policy_complete_panel(
    stack: dict[str, Any],
) -> None:
    requirement = triggers.required_consultation(
        triggers.DECISION_ENVELOPE_PUBLICATION,
        "env-1",
        initiator_persona_id=INITIATOR,
    )
    assert requirement is not None
    payload = {
        "request_type": "persona_policy",
        "requested_by": {"actor_type": "persona", "actor_id": INITIATOR},
        "from_persona_id": INITIATOR,
        "target_type": "envelope_publication",
        "target_id": "env-1",
        "metadata": {"panel": requirement.to_panel_metadata()},
        "trace_id": "trace-env-1",
    }

    def post(actor: str, body: dict[str, Any]) -> int:
        req = urllib.request.Request(
            stack["api"] + "/api/consult/requests",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {SERVICE_TOKEN}",
                "X-Pantheon-Tenant-Id": TENANT,
                "X-Pantheon-Service-Actor": actor,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    assert post("some-other-service", payload) == 403
    assert post("persona:persona-someone-else", payload) == 403
    stripped = json.loads(json.dumps(payload))
    stripped["metadata"]["panel"]["participants"] = [
        p for p in stripped["metadata"]["panel"]["participants"]
        if p["persona_id"] != triggers.RISK_PERSONA_ID
    ]
    assert post(f"persona:{INITIATOR}", stripped) == 422
    assert post(f"persona:{INITIATOR}", payload) == 201


def _get(base: str, path: str) -> Any:
    req = urllib.request.Request(
        base + path,
        headers={
            "Authorization": f"Bearer {SERVICE_TOKEN}",
            "X-Pantheon-Tenant-Id": TENANT,
            "X-Pantheon-Service-Actor": "panel-test",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())
