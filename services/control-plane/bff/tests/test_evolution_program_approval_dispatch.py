"""BFF command route -> real Evolution owner (token mode) -> real dispatch worker
tick -> real Research orchestrator -> ``_trigger_retrain_execution``.

What is real:
  * the BFF ``POST /bff/v1/commands`` admission stack and the real command
    executor / Evolution adapter, with an operator JWT;
  * ``services/governance/main.py`` with ``ApprovalDecisionStore`` and strict
    JWT admission (including ``GET /api/governance/approvals`` backing
    ``GET /bff/approvals``);
  * ``services/evolution/main.py`` with ``EVOLUTION_AUTH_MODE=token`` (its auth
    middleware runs on every call; the transport forwards the exact headers the
    caller emitted and rewrites nothing) and its real ``ApprovalReader`` /
    ``ProgramApprovalVerifier``;
  * the program store, decision store, dispatch outbox and ``dispatch_worker.run_poll``;
  * ``services/research/main.py`` including its real ``_trigger_retrain_execution``.

What is faked, and only this:
  * the training-session and registry HTTP boundary (``training.test`` /
    ``registry.test``), which ``_trigger_retrain_execution`` calls.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="evo_prog_e2e_")
os.environ.setdefault("EVOLUTION_DATA_DIR", _tmp)
os.environ.setdefault("INCIDENT_DATA_DIR", _tmp)
os.environ.setdefault("GOVERNANCE_DATA_DIR", os.path.join(_tmp, "governance"))
os.environ.setdefault("RESEARCH_ORCHESTRATOR_DATA_DIR", os.path.join(_tmp, "research"))

from services.control_plane.bff import command_executor  # noqa: E402
from services.control_plane.bff.auth.policy import (  # noqa: E402
    bff_error,
    extract_identity_jwt,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_adapters.router import create_command_adapters_router  # noqa: E402
from services.control_plane.bff.command_adapters.service import (  # noqa: E402
    CommandAdapterService,
    pop_command_auth_context,
)
from services.control_plane.bff.command_queue import CommandStore  # noqa: E402
from services.control_plane.bff.core.errors import register_error_handlers  # noqa: E402
from services.control_plane.bff.evolution.router import create_evolution_programs_router  # noqa: E402
from services.control_plane.bff.governance import approval_owner  # noqa: E402
from services.control_plane.bff.models import CommandType, utc_now  # noqa: E402
from services.control_plane.bff.ports.evolution_program_commands import (  # noqa: E402
    EvolutionServiceProgramCommandPort,
)
from services.control_plane.bff.tests.conftest import ApprovalDecisionReadSurface  # noqa: E402
from services.control_plane.governance.approval_decision import ApprovalDecisionStore  # noqa: E402
from services.evolution import dispatch_worker  # noqa: E402
from services.evolution import main as evo_main  # noqa: E402
from services.evolution.dispatch_outbox import (  # noqa: E402
    CompensationLedger,
    EvolutionDispatchOutbox,
    build_dispatch_outbox_store,
)
from services.evolution.dispatch_receipts import build_adapter_registry  # noqa: E402
from services.evolution.program_store import JsonProgramStore  # noqa: E402
from services.governance import main as gov_main  # noqa: E402
from services.research import main as research_main  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402

SERVICE_TOKEN = "pantheon-local-evolution-service"
TENANT = "tenant-dev"
JWT_ENV = {
    "PANTHEON_BFF_JWT_SECRET": "evo-prog-e2e-signing-secret-0123456789",
    "PANTHEON_BFF_JWT_ISSUER": "evo-prog-e2e-tests",
    "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
    "PANTHEON_BFF_AUTH_MODE": "strict",
}


def _jwt(sub: str, roles, tenant: str = TENANT) -> str:
    return encode_jwt_hs256(
        {"sub": sub, "roles": list(roles), "tenant_id": tenant, "exp": 4102444800,
         "iss": JWT_ENV["PANTHEON_BFF_JWT_ISSUER"], "aud": JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"]},
        secret=JWT_ENV["PANTHEON_BFF_JWT_SECRET"],
    )


class _Resp:
    def __init__(self, status: int, content: bytes) -> None:
        self.status = status
        self._io = io.BytesIO(content)
        self.headers = self

    def get_content_type(self) -> str:
        return "application/json"

    def read(self) -> bytes:
        return self._io.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Stack:
    def __init__(self, client, commands, gov_store, boundary, seen) -> None:
        self.client = client
        self.commands = commands
        self.gov_store = gov_store
        self.boundary = boundary
        self.seen = seen

    def command(self, token: str, action: str, program_id: str, key: str, **params):
        response = self.client.post(
            "/bff/v1/commands",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key, "X-Tenant-Id": TENANT},
            json={"command": "EvolutionProgramAction",
                  "target": {"type": "EvolutionProgram", "id": program_id},
                  "params": {"action_id": action, "program_id": program_id, **params},
                  "audit_context": {"reason": f"e2e {action}"}},
        )
        return response

    def result(self, response):
        assert response.status_code == 202, response.text
        record = self.commands.get_command(response.json()["data"]["command_id"])
        return record

    def decide_approval(
        self,
        approval_id: str,
        outcome: str = "approved",
        *,
        actor_id: str = "human-approver",
        actor_role: str = "governance_reviewer",
    ) -> dict:
        decider_jwt = _jwt(actor_id, [actor_role, "approver", "reviewer", "operator"])
        return approval_owner.decide(
            f"Bearer {decider_jwt}",
            approval_id,
            {
                "decision": outcome,
                "rationale": f"Human {outcome} evolution program in Governance",
                "actor_role": actor_role,
                "actor_id": actor_id,
                "expected_version": 1,
                "expires_at": "2099-01-01T00:00:00Z",
            },
            idempotency_key=f"gov-decide-{approval_id}-{outcome}",
        )


@pytest.fixture
def stack(monkeypatch, tmp_path):
    for key, value in JWT_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PANTHEON_GOVERNANCE_JWT_SECRET", JWT_ENV["PANTHEON_BFF_JWT_SECRET"])
    monkeypatch.setenv("PANTHEON_GOVERNANCE_JWT_ISSUER", JWT_ENV["PANTHEON_BFF_JWT_ISSUER"])
    monkeypatch.setenv("PANTHEON_GOVERNANCE_JWT_AUDIENCE", JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"])
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", TENANT)
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution.test")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://governance.test")
    monkeypatch.setenv("EVOLUTION_GOVERNANCE_BASE_URL", "http://governance.test")
    monkeypatch.setenv(
        "EVOLUTION_GOVERNANCE_SERVICE_TOKEN",
        _jwt("evolution-service-reader", ["approval_reader"]),
    )
    monkeypatch.setenv("TRAINING_SESSION_URL", "http://training.test")
    monkeypatch.setenv("REGISTRY_URL", "http://registry.test")

    monkeypatch.setattr(evo_main, "store", evo_main.EvolutionDecisionStore(
        storage_path=str(tmp_path / "decisions.json")))
    outbox = EvolutionDispatchOutbox(build_dispatch_outbox_store(data_dir=tmp_path))
    monkeypatch.setattr(evo_main, "dispatch_outbox", outbox)
    monkeypatch.setattr(evo_main, "compensation_ledger", CompensationLedger(data_dir=tmp_path))
    monkeypatch.setattr(evo_main.program_service, "_store", JsonProgramStore(str(tmp_path / "p.json")))
    registry = build_adapter_registry(research_api_url="http://research.test", timeout=5.0)
    monkeypatch.setattr(evo_main, "receipt_registry", registry)

    gov_store = ApprovalDecisionStore(storage_path=str(tmp_path / "approvals.json"))
    monkeypatch.setattr(gov_main, "store", gov_store)

    evolution = TestClient(evo_main.app)
    governance = TestClient(gov_main.app)
    research = TestClient(research_main.app)
    seen: list = []
    boundary: list = []
    retrain_calls: list = []
    original_retrain = research_main._trigger_retrain_execution
    monkeypatch.setattr(research_main, "_trigger_retrain_execution",
                        lambda run_id, params: retrain_calls.append(run_id) or original_retrain(run_id, params))

    def transport(req, timeout=30):
        url = req.full_url
        host = url.split("://", 1)[1].split("/", 1)[0]
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        headers = {k: v for k, v in req.header_items()}
        body = req.data
        method = req.get_method()
        if host == "evolution.test":
            seen.append({"path": path, "method": method, "headers": headers})
            resp = evolution.request(method, path, headers=headers, content=body)
        elif host == "research.test":
            resp = research.request(method, path, headers=headers, content=body)
        elif host == "governance.test":
            assert headers.get("Authorization", "").startswith("Bearer "), "credential must be forwarded"
            payload = json.loads(body) if body else None
            seen.append({"host": host, "path": path, "method": method, "payload": payload})
            resp = governance.request(method, path, headers=headers, content=body)
        elif host == "training.test":
            boundary.append(("training", method, path))
            payload = {"session_id": "ts-e2e-1"} if method == "POST" and path.endswith("/sessions") else {}
            return _Resp(200, json.dumps(payload).encode())
        elif host == "registry.test":
            boundary.append(("registry", method, path))
            if method == "GET":
                payload = {"entry": {"metadata": {"strategy_artifact": {
                    "parameters": {"lookback_bars": 2}, "version": "1.0.0"}}}}
            else:
                payload = {"registry_id": "art-e2e-v2"}
            return _Resp(200, json.dumps(payload).encode())
        else:  # pragma: no cover
            raise AssertionError(f"unexpected host {host}")
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(url, resp.status_code, "err", {}, io.BytesIO(resp.content))
        return _Resp(resp.status_code, resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", transport)

    commands = CommandStore(str(tmp_path / "commands.jsonl"))
    read_store = ApprovalDecisionReadSurface()
    read_store.get_evolution_program = lambda pid: evo_main.program_service.get_program(
        tenant_id=TENANT, program_id=pid
    )
    read_store.list_evolution_programs = lambda: evo_main.program_service.list_programs(
        tenant_id=TENANT
    )

    def process(command_id):
        record = commands.get_command(command_id)
        runtime_auth = pop_command_auth_context(command_id)
        audit = record.get("audit") or {}
        status, result, error = command_executor.execute_command_with_status(
            command_id, CommandType(record["type"]), dict(record["params"]),
            auth_token=runtime_auth.get("auth_token") or audit.get("auth_token"),
            mfa_token=runtime_auth.get("mfa_token") or audit.get("mfa_token"),
        )
        commands.update_status(command_id, status, result=result, error=error)

    service = CommandAdapterService(
        command_store=lambda: commands, read_surface=lambda: read_store,
        extract_identity=extract_identity_jwt, require_operator_role=require_operator_role,
        require_read_role=require_read_role, bff_error=bff_error, utc_now_fn=utc_now,
        validators={}, process_command_task=process,
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_command_adapters_router(service=service))
    app.include_router(
        create_evolution_programs_router(
            read_surface=lambda: read_store,
            extract_identity=extract_identity_jwt,
            require_read_role=require_read_role,
            require_operator_role=require_operator_role,
            bff_error=bff_error,
            utc_now=utc_now,
            program_commands=EvolutionServiceProgramCommandPort(),
        )
    )
    stack = Stack(TestClient(app), commands, gov_store, boundary, seen)
    stack.retrain_calls = retrain_calls
    stack.evolution = evolution
    stack.governance = governance
    stack.research = research
    stack.outbox = outbox
    yield stack


def _service_headers() -> dict:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}", "X-Tenant-Id": TENANT}


def _propose_retrain(stack: Stack, decision_id: str) -> None:
    created = stack.evolution.post("/api/evolution/proposals", headers=_service_headers(), json={
        "decision_id": decision_id, "tenant_id": TENANT, "target_type": "candidate_artifact",
        "target_id": "art-e2e-v1", "target_version": "1.0.0", "action_type": "retrain",
        "rationale": "Rolling drawdown breached the approved expected baseline.",
        "created_by_id": "evolution-daily-sweep", "linked_incident_id": f"inc-{decision_id}",
    })
    assert created.status_code == 201, created.text


def _auto_program(decision_id: str) -> str:
    created, _ = evo_main.program_service.create_auto_qualified_program(
        tenant_id=TENANT, actor_id="evolution-daily-sweep", name="e2e program",
        decision_id=decision_id, qualification={"policy_version": "e2e"})
    return created["program_id"]


def _tick(stack: Stack, *, later: bool = False):
    now = datetime.now(timezone.utc) + (timedelta(minutes=5) if later else timedelta(0))
    return dispatch_worker.run_poll(
        api_url="http://evolution.test", outbox=evo_main.dispatch_outbox,
        registry=evo_main.receipt_registry, compensations=evo_main.compensation_ledger,
        timeout_seconds=5.0, auth_token=SERVICE_TOKEN, now=now,
    )


def _wait_for_run(run_id: str, *, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    store = research_main.get_store()
    while time.time() < deadline:
        run = store.get_run(run_id)
        if run and run["status"] in {"completed", "failed", "rejected"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"research run {run_id} never reached a terminal status")


HUMAN = _jwt("human-approver", ["approver", "reviewer", "operator"])


def test_human_approved_program_runs_a_real_retrain_through_the_existing_chain(stack):
    decision_id = f"evo-e2e-{uuid.uuid4().hex[:8]}"
    _propose_retrain(stack, decision_id)
    program_id = _auto_program(decision_id)

    # 1. Submit: BFF files one durable governance request bound to tenant/program/revision
    #    and it appears in the approvals queue read by GET /bff/approvals; resubmitting
    #    with the same key reuses the existing request.
    submitted = stack.result(stack.command(HUMAN, "submit_evolution_review", program_id, "k-submit"))
    assert submitted["status"] == "executed", submitted
    filed = [s for s in stack.seen if s.get("path") == "/api/governance/approvals" and s.get("method") == "POST"]
    assert len(filed) == 1
    approval_id = filed[0]["payload"]["decision_id"]
    assert filed[0]["payload"]["target_type"] == "evolution_proposal"
    assert filed[0]["payload"]["target_id"] == program_id
    assert filed[0]["payload"]["tenant_id"] == TENANT

    queue = approval_owner.list_decisions(f"Bearer {HUMAN}", pending_only=True)
    assert [item["decision_id"] for item in queue] == [approval_id]
    assert queue[0]["target_id"] == program_id
    assert queue[0]["target_type"] == "evolution_proposal"

    resubmitted = stack.result(stack.command(HUMAN, "submit_evolution_review", program_id, "k-submit"))
    assert resubmitted["status"] == "executed"
    assert len(approval_owner.list_decisions(f"Bearer {HUMAN}", pending_only=True)) == 1

    # 2. Nothing is approved until a human decides: approve is refused (pending).
    refused = stack.result(stack.command(HUMAN, "approve_program", program_id, "k-approve-early"))
    assert refused["status"] != "executed"
    assert "approval_pending" in json.dumps(refused)
    assert evo_main.program_service.get_program(tenant_id=TENANT, program_id=program_id)["status"] == "under_review"
    assert stack.outbox.list_all(tenant_id=TENANT) == []

    # 3. A human decides in Governance; only then does approval go through.
    stack.decide_approval(approval_id, actor_id="human-approver", actor_role="governance_reviewer")
    approved = stack.result(stack.command(HUMAN, "approve_program", program_id, "k-approve"))
    assert approved["status"] == "executed", approved
    program = evo_main.program_service.get_program(tenant_id=TENANT, program_id=program_id)
    assert program["status"] == "active"
    decision = evo_main.store.get(decision_id)
    assert evo_main._enum_value(decision.decision_state) == "approved"
    assert decision.approval_decision_id == approval_id
    assert len(stack.outbox.list_all(tenant_id=TENANT)) == 1

    # 4. Real dispatch tick -> real research run -> real _trigger_retrain_execution.
    first = _tick(stack)
    assert first["claimed"] == 1 and first["errors"] == [], first
    run_id = first["items"][0]["downstream_ref_id"]
    assert run_id
    run = _wait_for_run(run_id)
    assert stack.retrain_calls == [run_id]
    assert run["status"] == "completed"
    assert [b[0] for b in stack.boundary] == ["training", "registry", "registry", "training"]

    # 5. Only the terminal receipt the worker reads back from the orchestrator
    #    converges the decision.  The retrain executor runs in a thread, so the
    #    first tick may still have seen it in flight; the next tick must then converge.
    if first["executed"] == 0:
        assert first["pending"] == 1
        assert evo_main._enum_value(evo_main.store.get(decision_id).decision_state) == "approved"
        second = _tick(stack, later=True)
        assert second["executed"] == 1, second
    assert evo_main._enum_value(evo_main.store.get(decision_id).decision_state) == "executed"

    # 6. The real run id is linked into the program and its status is read back.
    program = evo_main.program_service.get_program(tenant_id=TENANT, program_id=program_id)
    assert program["run_ids"] == [run_id]
    runs = stack.evolution.get(f"/api/evolution/programs/{program_id}/runs", headers=_service_headers()).json()
    assert [(r["run_id"], r["status"], r["source"]) for r in runs["items"]] == [
        (run_id, "success", "orchestrator_readback")]

    # Auth: every call that reached Evolution carried the service credential, never the operator JWT.
    for call in stack.seen:
        if call.get("headers"):
            assert call["headers"].get("Authorization") != f"Bearer {HUMAN}"
            assert call["headers"].get("X-tenant-id") == TENANT or call["headers"].get("X-Tenant-Id") == TENANT


def test_a_failed_run_stays_failed_and_is_shown_as_failed(stack, monkeypatch):
    decision_id = f"evo-e2e-{uuid.uuid4().hex[:8]}"
    _propose_retrain(stack, decision_id)
    program_id = _auto_program(decision_id)
    stack.result(stack.command(HUMAN, "submit_evolution_review", program_id, "k-submit-f"))
    approval_id = next(
        s for s in stack.seen if s.get("path") == "/api/governance/approvals" and s.get("method") == "POST"
    )["payload"]["decision_id"]
    stack.decide_approval(approval_id, actor_id="human-approver", actor_role="governance_reviewer")
    stack.result(stack.command(HUMAN, "approve_program", program_id, "k-approve-f"))

    original = urllib.request.urlopen

    def failing_registry(req, timeout=30):
        if req.full_url.startswith("http://registry.test"):
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b"{}"))
        return original(req, timeout=timeout)

    monkeypatch.setattr(urllib.request, "urlopen", failing_registry)
    first = _tick(stack)
    run_id = first["items"][0]["downstream_ref_id"]
    assert _wait_for_run(run_id)["status"] == "failed"
    _tick(stack, later=True)
    assert evo_main._enum_value(evo_main.store.get(decision_id).decision_state) != "executed"
    runs = stack.evolution.get(f"/api/evolution/programs/{program_id}/runs", headers=_service_headers()).json()
    assert [r["status"] for r in runs["items"]] == ["failed"]


@pytest.mark.parametrize("system_identity", ["evolution-daily-sweep", "evolution-dispatch-worker"])
def test_system_identities_cannot_approve_or_reject_on_the_bff_command_route(stack, system_identity):
    decision_id = f"evo-e2e-{uuid.uuid4().hex[:8]}"
    _propose_retrain(stack, decision_id)
    program_id = _auto_program(decision_id)
    stack.result(stack.command(HUMAN, "submit_evolution_review", program_id, "k-submit-s"))
    bot = _jwt(system_identity, ["approver", "admin", "operator"])
    for action, params in (("approve_program", {}), ("reject_program", {"reason": "no"})):
        response = stack.command(bot, action, program_id, f"k-{action}-{system_identity}", **params)
        assert response.status_code in (202, 403), response.text
        if response.status_code == 202:
            assert stack.result(response)["status"] != "executed"
    assert evo_main.program_service.get_program(tenant_id=TENANT, program_id=program_id)["status"] == "under_review"


def test_system_identities_are_refused_by_the_owner_route_itself(stack):
    decision_id = f"evo-e2e-{uuid.uuid4().hex[:8]}"
    _propose_retrain(stack, decision_id)
    program_id = _auto_program(decision_id)
    for system_identity in ("evolution-daily-sweep", "evolution-dispatch-worker"):
        for action in ("approve_program", "reject_program"):
            response = stack.evolution.post(
                f"/api/evolution/programs/{program_id}/actions/{action}", headers=_service_headers(),
                json={"actor_id": system_identity, "actor_role": "admin", "reason": "x"})
            assert response.status_code == 403, response.text


def test_runs_route_reads_orchestrator_status_through_the_command_port(stack):
    decision_id = f"evo-e2e-{uuid.uuid4().hex[:8]}"
    _propose_retrain(stack, decision_id)
    program_id = _auto_program(decision_id)
    import asyncio
    runs = asyncio.run(EvolutionServiceProgramCommandPort().list_program_runs(
        caller_token=f"Bearer {HUMAN}", tenant_id=TENANT, program_id=program_id))
    assert runs == {"items": []}  # nothing dispatched, so nothing is fabricated


def test_program_detail_allowed_actions_reflect_status_and_caller_role(stack):
    """GET /bff/evolution-programs/{program_id} returns allowedActions from the
    single canonical policy that reflects status and caller role."""
    decision_id = f"evo-e2e-{uuid.uuid4().hex[:8]}"
    _propose_retrain(stack, decision_id)
    program_id = _auto_program(decision_id)

    op_jwt = _jwt("operator-only", ["operator"])
    approver_jwt = _jwt("human-approver", ["approver", "operator"])
    sweep_jwt = _jwt("evolution-daily-sweep", ["approver", "operator"])

    # Draft: operator can submit_evolution_review
    draft_resp = stack.client.get(
        f"/bff/evolution-programs/{program_id}",
        headers={"Authorization": f"Bearer {op_jwt}"},
    )
    assert draft_resp.status_code == 200, draft_resp.text
    assert draft_resp.json()["data"]["allowedActions"] == ["submit_evolution_review"]

    # Submit for review
    stack.result(stack.command(HUMAN, "submit_evolution_review", program_id, "k-submit-actions"))

    # Under review: operator has [], approver has [approve_program, reject_program], system principal has []
    op_review = stack.client.get(
        f"/bff/evolution-programs/{program_id}",
        headers={"Authorization": f"Bearer {op_jwt}"},
    ).json()["data"]
    assert op_review["allowedActions"] == []

    approver_review = stack.client.get(
        f"/bff/evolution-programs/{program_id}",
        headers={"Authorization": f"Bearer {approver_jwt}"},
    ).json()["data"]
    assert approver_review["allowedActions"] == ["approve_program", "reject_program"]

    sweep_review = stack.client.get(
        f"/bff/evolution-programs/{program_id}",
        headers={"Authorization": f"Bearer {sweep_jwt}"},
    ).json()["data"]
    assert sweep_review["allowedActions"] == []
