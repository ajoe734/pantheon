"""EVOLUTION-BFF-FREEZE-EXEC-20261105: a direct EvolutionDecision reaches executed through the BFF.

One human decision drives proposed -> reviewed -> approved -> executed for a
paper freeze of a candidate_artifact:

  * the Evolution owner files the Governance ``evolution_execute`` request under
    its own propose-only principal (the BFF computes no digest);
  * the operator decides it once via ``POST /bff/approvals/{id}/decide``;
  * that one record serves ReviewMutation and ApproveEvolutionDecision;
  * ExecuteEvolutionAction gets the exact binding from the owner (which re-runs
    ``require_execution_approval``), records the FreezeOrder in Governance, then
    posts ``/execute`` with that FreezeOrder as the receipt.

What is real: the BFF command routes and command executor, the BFF
``/bff/approvals/{id}/decide`` route, ``services/governance/main.py`` (strict JWT
admission, JSON stores), ``services/evolution/main.py`` with
``EVOLUTION_AUTH_MODE=token`` (its auth middleware runs on every call), the
Evolution ``ApprovalReader``, the receipt adapter and the dispatch worker.
What is a seam, and only this: ``urllib.request.urlopen`` (and the reader's
opener) routes a host name to the in-process app and forwards the exact headers
the caller emitted without rewriting them.  Any other host fails the test, which
is how "no runtime, broker or capital call" is asserted.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="evo_freeze_e2e_")
os.environ.setdefault("EVOLUTION_DATA_DIR", _tmp)
os.environ.setdefault("INCIDENT_DATA_DIR", _tmp)
os.environ.setdefault("GOVERNANCE_DATA_DIR", os.path.join(_tmp, "governance"))

from services.control_plane.bff import command_executor  # noqa: E402
from services.control_plane.bff.auth.policy import (  # noqa: E402
    bff_error,
    capabilities_for_identity,
    extract_identity_jwt,
    require_operator_role,
    require_read_role,
)
from services.control_plane.bff.command_adapters.evolution_adapter import (  # noqa: E402
    create_evolution_decision_approval_router,
)
from services.control_plane.bff.command_adapters.router import create_command_adapters_router  # noqa: E402
from services.control_plane.bff.command_adapters.service import (  # noqa: E402
    CommandAdapterService,
    pop_command_auth_context,
)
from services.control_plane.bff.command_queue import CommandStore  # noqa: E402
from services.control_plane.bff.core.errors import register_error_handlers  # noqa: E402
from services.control_plane.bff.evolution.router import create_evolution_router  # noqa: E402
from services.control_plane.bff.governance import approval_owner  # noqa: E402
from services.control_plane.bff.governance.router import create_governance_router  # noqa: E402
from services.control_plane.bff.models import CommandType, redact_evidence_refs, utc_now  # noqa: E402
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
from services.governance import main as gov_main  # noqa: E402
from services.governance.record_store import JsonGovernanceRecordStore  # noqa: E402
from services.runtime_auth_inbound import encode_jwt_hs256  # noqa: E402

SERVICE_TOKEN = "pantheon-local-evolution-service"
TENANT = "tenant-dev"
OTHER_TENANT = "tenant-other"
JWT_ENV = {
    "PANTHEON_BFF_JWT_SECRET": "evo-freeze-e2e-signing-secret-0123456789",
    "PANTHEON_BFF_JWT_ISSUER": "evo-freeze-e2e-tests",
    "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
    "PANTHEON_BFF_AUTH_MODE": "strict",
}
OWNER_ROLES = ["operator", "risk_owner", "admin", "approver", "reviewer"]
ALLOWED_HOSTS = {"evolution.test", "governance.test"}


def _jwt(sub: str, roles, tenant: str = TENANT) -> str:
    return encode_jwt_hs256(
        {"sub": sub, "roles": list(roles), "tenant_id": tenant, "exp": 4102444800,
         "iss": JWT_ENV["PANTHEON_BFF_JWT_ISSUER"], "aud": JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"]},
        secret=JWT_ENV["PANTHEON_BFF_JWT_SECRET"],
    )


OPERATOR = _jwt("pantheon-dev-operator-a", OWNER_ROLES)
DECIDER = _jwt("pantheon-dev-admin-test", OWNER_ROLES)
PROPOSER = _jwt("evolution-execute-proposer", ["approval_proposer"])


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


class _GovernanceBackedReadSurface(ApprovalDecisionReadSurface):
    """The BFF approvals read model, reading the real Governance record."""

    def get_approval_decision(self, decision_id):
        try:
            return approval_owner.get_decision(f"Bearer {DECIDER}", decision_id)
        except urllib.error.HTTPError:
            return None


class Stack:
    pass


@pytest.fixture
def stack(monkeypatch, tmp_path):
    for key, value in JWT_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PANTHEON_GOVERNANCE_JWT_SECRET", JWT_ENV["PANTHEON_BFF_JWT_SECRET"])
    monkeypatch.setenv("PANTHEON_GOVERNANCE_JWT_ISSUER", JWT_ENV["PANTHEON_BFF_JWT_ISSUER"])
    monkeypatch.setenv("PANTHEON_GOVERNANCE_JWT_AUDIENCE", JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"])
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", f"{TENANT},{OTHER_TENANT}")
    monkeypatch.setenv("PANTHEON_EVOLUTION_API_URL", "http://evolution.test")
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "http://governance.test")
    monkeypatch.setenv("EVOLUTION_GOVERNANCE_BASE_URL", "http://governance.test")
    monkeypatch.setenv("EVOLUTION_GOVERNANCE_SERVICE_TOKEN", _jwt("evolution-service-reader", ["approval_reader"]))
    monkeypatch.setenv("EVOLUTION_EXECUTE_PROPOSER_TOKEN", PROPOSER)
    monkeypatch.delenv("EVOLUTION_EXECUTE_PROPOSER_TOKEN_FILE", raising=False)

    monkeypatch.setattr(evo_main, "store", evo_main.EvolutionDecisionStore(
        storage_path=str(tmp_path / "decisions.json")))
    outbox = EvolutionDispatchOutbox(build_dispatch_outbox_store(data_dir=tmp_path))
    monkeypatch.setattr(evo_main, "dispatch_outbox", outbox)
    monkeypatch.setattr(evo_main, "compensation_ledger", CompensationLedger(data_dir=tmp_path))
    monkeypatch.setattr(evo_main, "receipt_registry", build_adapter_registry(
        research_api_url="http://research.test", timeout=5.0, governance_api_url="http://governance.test"))
    monkeypatch.setattr(gov_main, "store", ApprovalDecisionStore(storage_path=str(tmp_path / "approvals.json")))
    freeze_store = JsonGovernanceRecordStore(str(tmp_path / "freeze.json"), id_fields=("freeze_order_id", "id"))
    monkeypatch.setattr(gov_main, "freeze_order_store", freeze_store)

    evolution = TestClient(evo_main.app)
    governance = TestClient(gov_main.app)
    s = Stack()
    s.seen, s.hosts, s.fail_once = [], [], {}

    def transport(req, timeout=30):
        url = req.full_url
        host = url.split("://", 1)[1].split("/", 1)[0]
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        method = req.get_method()
        s.hosts.append(host)
        assert host in ALLOWED_HOSTS, f"unexpected outbound call to {host}{path}"
        headers = {k: v for k, v in req.header_items()}
        payload = json.loads(req.data) if req.data else None
        s.seen.append({"host": host, "path": path, "method": method, "payload": payload})
        failing = s.fail_once.get((method, path.rsplit("/", 1)[-1] if path.endswith("/execute") else path))
        if failing:
            s.fail_once.pop((method, path.rsplit("/", 1)[-1] if path.endswith("/execute") else path))
            raise urllib.error.HTTPError(url, failing, "injected", {}, io.BytesIO(b"{}"))
        client = evolution if host == "evolution.test" else governance
        resp = client.request(method, path, headers=headers, content=req.data)
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(url, resp.status_code, "err", {}, io.BytesIO(resp.content))
        return _Resp(resp.status_code, resp.content)

    from services.governance import approval_authority

    class _Opener:
        open = staticmethod(transport)

    monkeypatch.setattr(urllib.request, "urlopen", transport)
    monkeypatch.setattr(approval_authority, "build_opener", lambda *handlers: _Opener())

    commands = CommandStore(str(tmp_path / "commands.jsonl"))
    read_store = _GovernanceBackedReadSurface()

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
    app.include_router(create_evolution_decision_approval_router(
        extract_identity=extract_identity_jwt,
        require_operator_role=require_operator_role,
        bff_error=bff_error))
    app.include_router(create_evolution_router(
        read_surface=lambda: read_store, extract_identity=extract_identity_jwt,
        require_read_role=require_read_role, require_operator_role=require_operator_role,
        bff_error=bff_error, utc_now=utc_now))
    app.include_router(create_governance_router(
        read_surface=read_store, extract_identity=extract_identity_jwt,
        require_read_role=require_read_role, require_operator_role=require_operator_role,
        bff_error=bff_error, redact_evidence_refs=redact_evidence_refs,
        capabilities_for_identity=capabilities_for_identity))
    s.client = TestClient(app)
    s.commands, s.evolution, s.governance = commands, evolution, governance
    s.outbox, s.freeze_store = outbox, freeze_store
    return s


def _service_headers(tenant: str = TENANT) -> dict:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}", "X-Tenant-Id": tenant}


def _propose(s: Stack, *, target_type="candidate_artifact", stage="paper", tenant=TENANT) -> str:
    decision_id = f"evo-fz-{uuid.uuid4().hex[:8]}"
    created = s.evolution.post("/api/evolution/proposals", headers=_service_headers(tenant), json={
        "decision_id": decision_id, "tenant_id": tenant, "target_type": target_type,
        "target_id": f"artifact-{decision_id}", "target_version": "v1", "action_type": "freeze",
        "target_stage": stage, "rationale": "drift incident inc-drift-fz",
        "created_by_id": "evolution-controller",
        "threshold_snapshots": [{
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.3", "signal_type": "feature_drift",
            "metric_name": "population_stability_index", "comparator": "gt",
            "observed_value": 0.31, "threshold_value": 0.30}],
    })
    assert created.status_code == 201, created.text
    return decision_id


def _command(s: Stack, token, command, decision_id, params, key):
    response = s.client.post(
        "/bff/v1/commands",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key, "X-Tenant-Id": TENANT},
        json={"command": command, "target": {"type": "EvolutionDecision", "id": decision_id},
              "params": params, "audit_context": {"reason": f"e2e {command}"}},
    )
    return response


def _run(s: Stack, response):
    assert response.status_code == 202, response.text
    return s.commands.get_command(response.json()["data"]["command_id"])


def _review(s, did, approval_id, key="k-review", token=OPERATOR, role="risk_owner"):
    return _command(s, token, "ReviewMutation", did,
                    {"decision_id": did, "approval_decision_id": approval_id, "actor_role": role}, key)


def _approve(s, did, approval_id, key="k-approve", token=OPERATOR, role="risk_owner"):
    return _command(s, token, "ApproveEvolutionDecision", did,
                    {"evolution_decision_id": did, "approval_action": "approve",
                     "approval_decision_id": approval_id, "actor_role": role}, key)


def _execute(s, did, key="k-execute", token=OPERATOR, role="risk_owner"):
    return _command(s, token, "ExecuteEvolutionAction", did,
                    {"evolution_decision_id": did, "action_type": "freeze", "actor_role": role,
                     "note": "paper quarantine after drift"}, key)


def _request_approval(s: Stack, did: str) -> str:
    response = s.client.post(f"/bff/evolution-decisions/{did}/execution-approval-request",
                             headers={"Authorization": f"Bearer {OPERATOR}"})
    assert response.status_code == 200, response.text
    return response.json()["approval_decision_id"]


def _decide(s: Stack, approval_id: str, outcome="approved", token=DECIDER, actor="pantheon-dev-admin-test"):
    return s.client.post(
        f"/bff/approvals/{approval_id}/decide",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": f"decide-{approval_id}-{outcome}"},
        json={"decision": outcome, "rationale": f"human {outcome}", "actor_role": "risk_owner",
              "actor_id": actor, "expected_version": 1, "expires_at": "2099-01-01T00:00:00Z"},
    )


def _state(did: str) -> str:
    return evo_main._enum_value(evo_main.store.get(did).decision_state)


def _tick(*, later: bool = False):
    now = datetime.now(timezone.utc) + (timedelta(minutes=5) if later else timedelta(0))
    return dispatch_worker.run_poll(
        api_url="http://evolution.test", outbox=evo_main.dispatch_outbox,
        registry=evo_main.receipt_registry, compensations=evo_main.compensation_ledger,
        timeout_seconds=5.0, auth_token=SERVICE_TOKEN, now=now,
    )


def _freeze_orders(s: Stack):
    return s.freeze_store.list_all()


def _approved_decision(s: Stack):
    did = _propose(s)
    approval_id = _request_approval(s, did)
    assert _decide(s, approval_id).status_code == 202
    assert _run(s, _review(s, did, approval_id))["status"] == "executed"
    assert _run(s, _approve(s, did, approval_id))["status"] == "executed"
    assert _state(did) == "approved"
    return did, approval_id


# ---------------------------------------------------------------------------


def test_one_human_decision_drives_proposed_to_executed(stack):
    s = stack
    did = _propose(s)
    digest = evo_main.immutable_decision_fingerprint(evo_main.store.get(did).to_dict())

    # The owner files the request under the propose-only principal and computes the binding.
    approval_id = _request_approval(s, did)
    filed = [c for c in s.seen if c["host"] == "governance.test" and c["path"] == "/api/governance/approvals"
             and c["method"] == "POST"]
    assert len(filed) == 1
    body = filed[0]["payload"]
    assert body["target_type"] == "evolution_execute" and body["target_id"] == did
    assert body["owner_user_id"] == "evolution-execute-proposer" and body["tenant_id"] == TENANT
    assert body["subject"] == {"proposal_id": did, "proposal_content_digest": digest}
    assert not any(k in json.dumps(c.get("payload") or {}) for c in s.seen
                   if c["host"] == "evolution.test" and c["path"].endswith("execution-approval-request")
                   for k in (digest,)), "the BFF must not compute or send a digest"

    # A resubmission reuses the same request.
    assert _request_approval(s, did) == approval_id
    pending = approval_owner.get_decision(f"Bearer {DECIDER}", approval_id)
    assert pending["decision_state"] == "proposed"

    # Nothing is reviewable before the human decides.
    early = _review(s, did, approval_id, key="k-early")
    assert early.status_code == 409 and "APPROVAL_DECISION_NOT_APPROVED" in early.text
    assert _state(did) == "proposed"

    # One human decision (decider is not the proposer) serves review and approve.
    assert _decide(s, approval_id).status_code == 202
    assert _run(s, _review(s, did, approval_id))["status"] == "executed"
    assert _run(s, _approve(s, did, approval_id))["status"] == "executed"
    assert _state(did) == "approved"
    assert _freeze_orders(s) == []

    # The worker holds the approved governance-plane decision: no submit, no failed attempt, no dead letter.
    held = _tick()
    assert held["held"] == 1 and held["errors"] == [] and held["dead_lettered"] == 0 and held["retried"] == 0
    assert held["items"][0]["disposition"] == "held"
    record = s.outbox.list_all(tenant_id=TENANT)[0]
    assert record.status.value == "pending"  # still deliverable once executed; never failed or dead-lettered
    assert _freeze_orders(s) == []

    # Execute: binding -> FreezeOrder (bound) -> /execute with that receipt.
    result = _run(s, _execute(s, did))
    assert result["status"] == "executed", result
    receipt = result["result"]
    freeze_id = f"freeze-{did}"
    assert receipt["decision_state"] == "executed"
    assert receipt["execution_ref_id"] == freeze_id
    assert receipt["governance_quarantine_only"] is True and receipt["trading_stopped"] is False
    assert "does not stop trading" in receipt["notice"]
    assert receipt["cooldown_ends_at"] and receipt["observation_window_ends_at"]

    orders = _freeze_orders(s)
    assert [o["freeze_order_id"] for o in orders] == [freeze_id]
    order = orders[0]
    assert order["scope"] == "artifact" and order["status"] == "active"
    assert order["evolution_decision_id"] == did and order["tenant_id"] == TENANT
    assert order["target_id"] == f"artifact-{did}" and order["target_version"] == "v1"
    assert order["proposal_content_digest"] == digest and order["approval_decision_id"] == approval_id

    # Order of calls: binding read, then the FreezeOrder write, then /execute; never a write after /execute.
    paths = [(c["method"], c["path"]) for c in s.seen if c["host"] == "evolution.test" or "freeze" in c["path"]]
    binding = paths.index(("GET", f"/api/evolution/proposals/{did}/receipt-binding"))
    write = paths.index(("POST", "/api/governance/freeze-orders"))
    execute = paths.index(("POST", f"/api/evolution/proposals/{did}/execute"))
    assert binding < write < execute
    assert paths.count(("POST", "/api/governance/freeze-orders")) == 1
    execute_call = next(c for c in s.seen if c["path"].endswith("/execute"))
    assert execute_call["payload"]["execution_receipt"] == {
        "downstream_kind": "governance_freeze_order", "downstream_ref_id": freeze_id}

    final = evo_main.store.get(did)
    assert final.execution_result.execution_ref_id == freeze_id
    assert final.cooldown_ends_at and final.observation_window_ends_at

    # Converged: the existing path publishes the outbox record.
    converged = _tick(later=True)
    assert converged["items"][0]["disposition"] == "already_executed" and converged["errors"] == []

    # Zero runtime / broker / capital calls: every outbound host was Evolution or Governance.
    assert set(s.hosts) <= ALLOWED_HOSTS


def test_retry_after_partial_failure_reuses_the_freeze_order(stack):
    s = stack
    did, _ = _approved_decision(s)
    s.fail_once[("POST", "execute")] = 503
    first = _run(s, _execute(s, did, key="k-exec-1"))
    assert first["status"] == "failed"
    assert _state(did) == "approved"
    assert len(_freeze_orders(s)) == 1  # the FreezeOrder was written before /execute failed

    second = _run(s, _execute(s, did, key="k-exec-2"))
    assert second["status"] == "executed", second
    assert second["result"]["freeze_order_reused"] is True
    assert [o["freeze_order_id"] for o in _freeze_orders(s)] == [f"freeze-{did}"]
    assert _state(did) == "executed"
    executed_at = evo_main.store.get(did).cooldown_ends_at

    # A further retry after executed neither executes again nor moves the cooldown.
    third = _run(s, _execute(s, did, key="k-exec-3"))
    assert third["status"] == "executed" and third["result"]["decision_state"] == "executed"
    assert evo_main.store.get(did).cooldown_ends_at == executed_at
    assert len(_freeze_orders(s)) == 1
    assert len([c for c in s.seen if c["path"].endswith("/execute") and c["method"] == "POST"]) == 2


def test_governance_write_failure_leaves_the_decision_approved_without_a_freeze_order(stack):
    s = stack
    did, _ = _approved_decision(s)
    s.fail_once[("POST", "/api/governance/freeze-orders")] = 503
    result = _run(s, _execute(s, did))
    assert result["status"] == "failed"
    assert _state(did) == "approved" and _freeze_orders(s) == []
    assert not [c for c in s.seen if c["path"].endswith("/execute")]


def test_evolution_proposal_record_cannot_review_or_execute(stack):
    s = stack
    did = _propose(s)
    # An evolution_proposal record filed and decided in Governance can never bind a direct decision.
    proposal_id = f"apv-prop-{uuid.uuid4().hex[:6]}"
    created = s.governance.post("/api/governance/approvals", headers={
        "Authorization": f"Bearer {PROPOSER}", "Idempotency-Key": proposal_id}, json={
        "decision_id": proposal_id, "expected_version": 0, "tenant_id": TENANT,
        "owner_user_id": "evolution-execute-proposer", "target_type": "evolution_proposal",
        "target_id": did, "target_version": "v1", "risk_level": "medium",
        "proposal_id": did, "proposal_revision": 1, "proposal_content_digest": "d" * 64,
        "expires_at": "2099-01-01T00:00:00Z"})
    assert created.status_code == 201, created.text
    assert _decide(s, proposal_id).status_code == 202
    refused = _review(s, did, proposal_id)
    assert refused.status_code == 409 and "APPROVAL_DECISION_BINDING_MISMATCH" in refused.text
    assert _state(did) == "proposed" and _freeze_orders(s) == []


@pytest.mark.parametrize("outcome", ["pending", "rejected"])
def test_pending_or_rejected_record_is_refused_before_any_owner_write(stack, outcome):
    s = stack
    did = _propose(s)
    approval_id = _request_approval(s, did)
    if outcome == "rejected":
        assert _decide(s, approval_id, "rejected").status_code == 202
    owner_writes_before = len([c for c in s.seen if c["host"] == "evolution.test" and c["method"] == "POST"])
    refused = _review(s, did, approval_id)
    assert refused.status_code == 409, refused.text
    assert "APPROVAL_DECISION_NOT_APPROVED" in refused.text
    assert len([c for c in s.seen if c["host"] == "evolution.test" and c["method"] == "POST"]) == owner_writes_before
    assert _state(did) == "proposed" and _freeze_orders(s) == []


def test_another_tenant_cannot_use_the_decision_or_its_approval(stack):
    s = stack
    did = _propose(s)
    approval_id = _request_approval(s, did)
    assert _decide(s, approval_id).status_code == 202
    foreign = _jwt("pantheon-dev-operator-a", OWNER_ROLES, tenant=OTHER_TENANT)
    response = _review(s, did, approval_id, token=foreign)
    record = _run(s, response) if response.status_code == 202 else None
    assert record is None or record["status"] != "executed"
    assert _state(did) == "proposed" and _freeze_orders(s) == []

    # A same-id approval from another tenant's proposer never lets the decision execute.
    approved_did = _propose(s)
    other_id = f"apv-other-{uuid.uuid4().hex[:6]}"
    other_proposer = _jwt("evolution-execute-proposer", ["approval_proposer"], tenant=OTHER_TENANT)
    created = s.governance.post("/api/governance/approvals", headers={
        "Authorization": f"Bearer {other_proposer}", "Idempotency-Key": other_id}, json={
        "decision_id": other_id, "expected_version": 0, "tenant_id": OTHER_TENANT,
        "owner_user_id": "evolution-execute-proposer", "target_type": "evolution_execute",
        "target_id": approved_did, "target_version": "v1", "risk_level": "medium",
        "expires_at": "2099-01-01T00:00:00Z",
        "subject": {"proposal_id": approved_did, "proposal_content_digest": "e" * 64}})
    assert created.status_code == 201, created.text
    other_decider = _jwt("pantheon-dev-admin-test", OWNER_ROLES, tenant=OTHER_TENANT)
    assert _decide(s, other_id, token=other_decider).status_code == 202
    # The other tenant's record is invisible to this tenant's BFF read: refused at admission.
    for step in (_review, _approve):
        refused = step(s, approved_did, other_id, key=f"k-{step.__name__}-other")
        assert refused.status_code == 409 and "APPROVAL_DECISION_NOT_FOUND" in refused.text
    assert _state(approved_did) == "proposed" and _freeze_orders(s) == []


def test_changed_proposal_digest_never_executes(stack):
    s = stack
    did = _propose(s)
    bad_id = f"apv-digest-{uuid.uuid4().hex[:6]}"
    created = s.governance.post("/api/governance/approvals", headers={
        "Authorization": f"Bearer {PROPOSER}", "Idempotency-Key": bad_id}, json={
        "decision_id": bad_id, "expected_version": 0, "tenant_id": TENANT,
        "owner_user_id": "evolution-execute-proposer", "target_type": "evolution_execute",
        "target_id": did, "target_version": "v1", "risk_level": "medium",
        "expires_at": "2099-01-01T00:00:00Z",
        "subject": {"proposal_id": did, "proposal_content_digest": "f" * 64}})
    assert created.status_code == 201, created.text
    assert _decide(s, bad_id).status_code == 202
    assert _run(s, _review(s, did, bad_id))["status"] == "executed"
    assert _run(s, _approve(s, did, bad_id))["status"] == "executed"
    assert _state(did) == "approved"
    refused = _run(s, _execute(s, did))
    assert refused["status"] == "failed"
    assert _state(did) == "approved" and _freeze_orders(s) == []
    assert not [c for c in s.seen if c["method"] == "POST" and c["path"] == "/api/governance/freeze-orders"]


def test_freeze_outside_paper_artifact_scope_is_refused_by_name(stack):
    s = stack
    did = _propose(s, target_type="candidate_artifact", stage="live")
    approval_id = _request_approval(s, did)
    assert _decide(s, approval_id).status_code == 202
    role = "governance_committee"
    committee = _jwt("pantheon-dev-operator-a", [*OWNER_ROLES, role])
    assert _run(s, _review(s, did, approval_id, role=role, token=committee))["status"] == "executed"
    assert _run(s, _approve(s, did, approval_id, role=role, token=committee))["status"] == "executed"
    assert _state(did) == "approved"
    refused = _run(s, _execute(s, did, role=role, token=committee))
    assert refused["status"] == "failed"
    assert "unsupported_freeze_scope" in json.dumps(refused)
    assert _freeze_orders(s) == []
    assert _state(did) != "executed"
