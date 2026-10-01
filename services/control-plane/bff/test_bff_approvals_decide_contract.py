"""Mounted contract: every BFF approval entry point forwards to the Governance owner.

A real HTTP stub stands in for the Governance owner (tenant from the forwarded JWT,
expected_version CAS, Idempotency-Key replay, two-vote target). The BFF router and
command adapters are the production code; no local approval state may answer.
"""
from __future__ import annotations

import base64
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import bff_error, require_operator_role, require_read_role
from services.control_plane.bff.command_adapters.governance_adapter import GovernanceCommandAdapter
from services.control_plane.bff.command_executor import execute_command
from services.control_plane.bff.models import CommandType
from services.control_plane.bff.governance.approval_owner import UnsupportedApprovalAction
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.ports import create_in_memory_read_surface_ports


def jwt(sub: str, tenant: str, *roles: str) -> str:
    part = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'none'})}.{part({'sub': sub, 'tenant_id': tenant, 'roles': list(roles)})}.sig"


class Owner:
    """Stub owner state + request log, shared with the handler."""

    def __init__(self):
        self.rows, self.receipts, self.calls = {}, {}, []


def make_handler(owner: Owner):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _claims(self):
            segment = self.headers["Authorization"].split(" ", 1)[1].split(".")[1]
            return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))

        def _handle(self, method):
            path = urlsplit(self.path).path
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or "null")
            claims, key = self._claims(), self.headers.get("Idempotency-Key")
            owner.calls.append((method, path, body, key, self.headers["Authorization"]))
            tenant = claims["tenant_id"]
            parts = path.split("/")
            if method == "GET" and path == "/api/governance/approvals":
                return self._send(200, [r for r in owner.rows.values() if r["tenant_id"] == tenant])
            if method == "GET":
                row = owner.rows.get(parts[-1])
                return self._send(200, row) if row and row["tenant_id"] == tenant else self._send(404, {"detail": "Approval decision not found"})
            if method == "POST" and path == "/api/governance/approvals":
                row = {"decision_id": body.get("decision_id") or f"apv-{key}", "decision_state": "proposed", "decision": None,
                       "version": 1, "evidence_refs": [], "tenant_id": body["tenant_id"], "owner_user_id": body["owner_user_id"],
                       "target_type": body["target_type"], "target_id": body["target_id"]}
                owner.rows[row["decision_id"]] = row
                return self._send(201, row)
            row = owner.rows.get(parts[-2])
            if not row or row["tenant_id"] != tenant:
                return self._send(404, {"detail": "Approval decision not found"})
            if body.get("outcome") not in ("approved", "rejected", "approved_with_conditions"):
                return self._send(422, {"detail": "Input should be 'approved', 'rejected' or 'approved_with_conditions'"})
            if (tenant, key) in owner.receipts:
                return self._send(200, owner.receipts[(tenant, key)])
            if body["expected_version"] != row["version"]:
                return self._send(409, {"detail": "Approval base version is stale"})
            if body["actor_id"] != claims["sub"] or body["actor_role"] not in claims["roles"]:
                return self._send(403, {"detail": "Body actor and role must match verified principal"})
            row["votes"] = row.get("votes", []) + [body["actor_id"]]
            row["version"] += 1
            row["decision_state"] = "decided" if len(row["votes"]) >= 2 else "under_review"
            row["decision"] = body["outcome"] if row["decision_state"] == "decided" else None
            owner.receipts[(tenant, key)] = dict(row)
            return self._send(200, row)

        do_GET = lambda self: self._handle("GET")
        do_POST = lambda self: self._handle("POST")

    return Handler


@pytest.fixture()
def owner(monkeypatch):
    state = Owner()
    server = HTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", f"http://127.0.0.1:{server.server_port}")
    state.rows["a1"] = {"decision_id": "a1", "decision_state": "proposed", "decision": None, "version": 1, "tenant_id": "tenant-a",
                        "evidence_refs": [{"ref_type": "report", "ref_id": "r1"}], "owner_user_id": "op-1"}
    state.rows["b1"] = {**state.rows["a1"], "decision_id": "b1", "tenant_id": "tenant-b"}
    yield state
    server.shutdown()


@pytest.fixture()
def client(owner):
    def identity(authorization=None):
        claims = json.loads(base64.urlsafe_b64decode(authorization.split(".")[1] + "=="))
        return SimpleNamespace(operator_id=claims["sub"], roles=set(claims["roles"]), tenant_id=claims["tenant_id"])

    app = FastAPI()
    app.include_router(create_governance_router(
        read_surface=create_in_memory_read_surface_ports(), extract_identity=identity,
        require_read_role=require_read_role, require_operator_role=require_operator_role, bff_error=bff_error,
    ))
    return TestClient(app, raise_server_exceptions=False)


def headers(sub="rev-1", tenant="tenant-a", role="governance_reviewer", key="k1"):
    return {"Authorization": "Bearer " + jwt(sub, tenant, role), "Idempotency-Key": key}


def vote(version=1, **extra):
    return {"decision": "approve", "memo": "reviewed", "expected_version": version, **extra}


def test_reads_forward_jwt_and_project_thin_aliases(client, owner):
    listed = client.get("/api/v1/approval-decisions", headers=headers()).json()["data"]
    assert [item["id"] for item in listed] == ["a1"] and listed[0]["status"] == "pending" and listed[0]["version"] == 1
    assert client.get("/api/v1/approval-decisions/a1", headers=headers()).json()["data"]["decision_id"] == "a1"
    assert client.get("/bff/approvals", headers=headers()).json()["count"] == 1
    assert client.get("/bff/approvals/a1", headers=headers()).json()["data"]["version"] == 1
    assert client.get("/bff/approvals/a1/evidence", headers=headers()).json()["evidence"][0]["ref_id"] == "r1"
    assert {call[4] for call in owner.calls} == {headers()["Authorization"]}


def test_cross_tenant_reads_and_votes_are_denied_by_owner(client, owner):
    for response in (client.get("/bff/approvals/b1", headers=headers()),
                     client.get("/api/v1/approval-decisions/b1", headers=headers()),
                     client.get("/bff/approvals/b1/evidence", headers=headers()),
                     client.post("/bff/approvals/b1/decide", json=vote(), headers=headers())):
        assert response.status_code == 404
    assert owner.rows["b1"]["version"] == 1


def test_first_vote_is_pending_second_decides_stale_conflicts_and_replay_is_stable(client, owner):
    first = client.post("/bff/approvals/a1/decide", json=vote(), headers=headers(key="v1"))
    assert first.status_code == 202 and first.json()["decision_state"] == "under_review"
    assert first.json()["status"] == "pending" and first.json()["version"] == 2
    method, path, body, key, _ = owner.calls[-1]
    assert (path, key, body["expected_version"], body["actor_id"], body["actor_role"], body["outcome"]) == (
        "/api/governance/approvals/a1/decide", "v1", 1, "rev-1", "governance_reviewer", "approved")
    assert client.post("/bff/approvals/a1/decide", json=vote(), headers=headers(sub="rk-1", key="v2")).status_code == 409
    assert client.post("/bff/approvals/a1/decide", json=vote(), headers=headers(key="v1")).json()["version"] == 2
    second = client.post("/bff/approvals/a1/decide", json=vote(2), headers=headers(sub="rk-1", role="risk_owner", key="v3"))
    assert second.json()["decision_state"] == "decided" and second.json()["outcome"] == "approved"


@pytest.mark.parametrize("payload", [{"decision": "escalate"}, {"decision": "freeze"}, {"decision": "stage"}])
def test_unsupported_verbs_are_explicit_and_never_become_votes(client, owner, payload):
    calls = len(owner.calls)
    response = client.post("/bff/approvals/a1/decide", json=vote(**payload), headers=headers())
    assert response.status_code == 501 and response.json()["detail"]["error"]["code"] == "NOT_IMPLEMENTED"
    assert len(owner.calls) == calls


@pytest.mark.parametrize("verb", ["request_revision", "requestrevision", "RequestApprovalRevision"])
def test_revision_is_retired_with_410_and_no_owner_call(client, owner, verb):
    calls_before = len(owner.calls)
    response = client.post("/bff/approvals/a1/decide", json=vote(decision=verb), headers=headers(key=f"rev-{verb}"))
    assert response.status_code == 410
    detail = response.json()["detail"]["error"]
    assert detail["code"] == "VALIDATION_FAILED"
    assert "RequestApprovalRevision is retired" in detail["message"]
    assert "RejectDecision with notes" in str(detail)
    assert len(owner.calls) == calls_before


def test_vote_requires_version_and_owner_decides_authority(client, owner):
    missing = client.post("/bff/approvals/a1/decide", json={"decision": "approve", "memo": "reviewed"}, headers=headers())
    assert missing.status_code == 422
    forged = client.post("/bff/approvals/a1/decide", json=vote(actor_role="risk_owner"), headers=headers())
    assert forged.status_code == 403 and owner.rows["a1"]["version"] == 1


def test_batch_decide_forwards_each_item_with_stable_keys(client, owner):
    response = client.post("/bff/approvals/batch-decide", headers=headers(key="batch"),
                           json={"decisions": [{"id": "a1", **vote()}, {"id": "b1", **vote()}, {"id": "a1", **vote(decision="freeze")}]})
    results = response.json()["results"]
    assert response.status_code == 207 and [r["status"] for r in results] == ["accepted", "failed", "failed"]
    assert results[1]["http_status"] == 404
    assert [c[3] for c in owner.calls if c[0] == "POST"] == ["batch::0::a1", "batch::1::b1"]


def test_create_forwards_complete_proposal_and_rejects_legacy_create(client, owner):
    proposal = {"target_type": "registry_entry", "target_id": "art-1", "target_version": "1", "tenant_id": "tenant-a",
                "owner_user_id": "op-1", "expected_version": 0}
    created = client.post("/api/v1/approval-decisions", json=proposal, headers=headers(sub="op-1", role="operator", key="c1"))
    assert created.status_code == 201 and created.json()["decision_state"] == "proposed" and owner.rows[created.json()["decision_id"]]
    legacy = client.post("/api/v1/approval-decisions", json={"plan_id": "p", "decision": "approve", "memo": "legacy memo"},
                         headers=headers(key="c2"))
    assert legacy.status_code == 501 and legacy.json()["detail"]["error"]["code"] == "NOT_IMPLEMENTED"
    assert client.post("/api/v1/approval-decisions", json=proposal, headers={"Authorization": headers()["Authorization"]}).status_code == 422


@pytest.fixture()
def command_client(owner, tmp_path):
    """Production command route (admission + preconditions + worker) mounted over the owner stub."""
    from services.control_plane.bff.command_adapters.preconditions import build_default_validators
    from services.control_plane.bff.command_adapters.router import create_command_adapters_router
    from services.control_plane.bff.command_adapters.service import CommandAdapterService
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.core.errors import register_error_handlers
    from services.control_plane.bff.models import utc_now

    def identity(authorization=None, mfa_token=None, **_):
        claims = json.loads(base64.urlsafe_b64decode(authorization.split(".")[1] + "=="))
        return SimpleNamespace(operator_id=claims["sub"], roles=list(claims["roles"]), tenant_id=claims["tenant_id"],
                               mfa_verified=True, token_kind="jwt")

    store = CommandStore(str(tmp_path / "commands.jsonl"))
    read_surface = create_in_memory_read_surface_ports()
    service = CommandAdapterService(
        command_store=store, read_surface=read_surface, extract_identity=identity,
        require_operator_role=require_operator_role, require_read_role=require_read_role, bff_error=bff_error,
        utc_now_fn=utc_now,
        validators=build_default_validators(read_surface=lambda: read_surface, ops_read_model_fn=None,
                                            bff_error_fn=bff_error, utc_now_fn=utc_now),
    )
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_command_adapters_router(service=service))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("command,params", [
    ("ApproveDecision", {"approval_notes": "ok"}),
    ("RejectDecision", {"rejection_reason": "no"}),
])
def test_mounted_command_route_forwards_to_owner_without_bff_role_gate(command_client, owner, command, params):
    body = {"command": command, "target": {"type": "ApprovalDecision", "id": "a1"},
            "params": {"decision_id": "a1", "expected_version": 1, **params}, "audit_context": {"reason": "review"}}
    auth = {"Authorization": "Bearer " + jwt("rev-1", "tenant-a", "operator", "governance_reviewer")}
    admitted = command_client.post("/bff/v1/commands", headers={**auth, "Idempotency-Key": f"m-{command}"}, json=body)
    assert admitted.status_code in (200, 201, 202), admitted.text
    assert owner.rows["a1"]["version"] == 2 and owner.calls[-1][4] == auth["Authorization"]

    cross = {**body, "target": {"type": "ApprovalDecision", "id": "b1"}, "params": {**body["params"], "decision_id": "b1"}}
    denied = command_client.post("/bff/v1/commands", headers={**auth, "Idempotency-Key": f"x-{command}"}, json=cross)
    assert owner.rows["b1"]["version"] == 1 and owner.rows["b1"]["decision_state"] == "proposed"
    assert denied.status_code in (200, 201, 202, 403, 404)


def test_command_entry_points_forward_the_original_jwt(owner):
    token = headers()["Authorization"]
    approved = execute_command("cmd-1", CommandType.APPROVE_DECISION,
                               {"decision_id": "a1", "expected_version": 1, "approval_notes": "ok"}, auth_token=token)
    assert approved["status"] == "under_review" and owner.calls[-1][3] == "cmd-1" and owner.calls[-1][4] == token
    with pytest.raises(urllib.error.HTTPError) as stale:
        execute_command("cmd-2", CommandType.REJECT_DECISION,
                        {"decision_id": "a1", "expected_version": 1, "rejection_reason": "no"}, auth_token=token)
    assert stale.value.code == 409
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as revision:
        execute_command("cmd-3", CommandType.REQUEST_APPROVAL_REVISION,
                        {"decision_id": "a1", "expected_version": 2, "revision_notes": "rework"}, auth_token=token)
    assert revision.value.status_code == 410 and "RejectDecision with notes" in str(revision.value.detail)

    adapter = GovernanceCommandAdapter()
    receipt = adapter.execute("cmd-4", "ReviewAction", {"decision_id": "a1", "action": "reject", "expected_version": 2,
                                                       "rejection_reason": "no"}, auth_token=token)
    assert receipt["status"] == "under_review" or receipt["status"] == "decided"
    assert receipt["authoritative_readback"]["version"] == 3
    with pytest.raises(UnsupportedApprovalAction):
        adapter.execute("cmd-5", "ReviewAction", {"decision_id": "a1", "action": "escalate"}, auth_token=token)
    with pytest.raises(HTTPException) as revision:
        adapter.execute("cmd-6", "RequestApprovalRevision", {"decision_id": "a1", "expected_version": 3, "revision_notes": "rework"},
                        auth_token=token)
    assert revision.value.status_code == 410 and "RejectDecision with notes" in str(revision.value.detail)
    adapter.execute("cmd-7", "ApproveDecision", {"decision_id": "a1", "expected_version": 3, "approval_notes": "ok"}, auth_token=token)
    assert owner.calls[-1][1].endswith("/a1/decide")


@pytest.mark.parametrize("command,conflict", [("RejectDecision", "approved"), ("ApproveDecision", "rejected")])
def test_command_verb_never_conflicts_with_outcome_param(owner, command, conflict):
    calls = len(owner.calls)
    with pytest.raises(ValueError):
        GovernanceCommandAdapter().execute("cmd-x", command, {"decision_id": "a1", "expected_version": 1, "outcome": conflict,
                                                              "approval_notes": "n", "rejection_reason": "n"},
                                           auth_token=headers()["Authorization"])
    assert len(owner.calls) == calls and owner.rows["a1"]["version"] == 1


def test_conflicting_outcome_and_decision_on_rest_is_rejected_before_owner(client, owner):
    calls = len(owner.calls)
    response = client.post("/bff/approvals/a1/decide", json=vote(outcome="rejected"), headers=headers())
    assert response.status_code == 422 and len(owner.calls) == calls


def test_dry_run_create_is_rejected_without_owner_mutation(client, owner):
    proposal = {"target_type": "registry_entry", "target_id": "art-1", "tenant_id": "tenant-a", "owner_user_id": "op-1"}
    before = len(owner.rows)
    response = client.post("/api/v1/approval-decisions", json=proposal, headers={**headers(sub="op-1", role="operator"), "X-Dry-Run": "true"})
    assert response.status_code == 501 and len(owner.rows) == before and not [c for c in owner.calls if c[0] == "POST"]


def test_batch_decide_returns_owner_result_unrewritten(client, owner):
    response = client.post("/bff/approvals/batch-decide", headers=headers(key="batch2"), json={"decisions": [{"id": "a1", **vote()}]})
    result = response.json()["results"][0]["result"]
    assert result["version"] == 2 and result["decision_state"] == "under_review"


@pytest.fixture()
def review_client(tmp_path):
    from services.control_plane.bff.command_adapters.service import CommandAdapterService
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.governance.router import create_governance_router
    from services.control_plane.bff.models import OperatorIdentity

    store = CommandStore(str(tmp_path / "review-store.jsonl"))
    identity = OperatorIdentity(operator_id="rev-1", roles=["operator", "governance_reviewer"], mfa_verified=True, claims={"tenant_id": "tenant-a"})
    svc = CommandAdapterService(command_store=store, read_surface=None, extract_identity=lambda *a, **k: identity)
    app = FastAPI()
    app.include_router(create_governance_router(extract_identity=lambda *a, **k: identity, submit_action=svc.submit_governance_action, command_store=store))
    return TestClient(app, raise_server_exceptions=False), store


@pytest.mark.parametrize("verb", ["request_revision", "requestrevision", "RequestApprovalRevision"])
def test_review_action_routes_retire_revision_with_410(review_client, owner, verb):
    client, store = review_client
    calls_before = len(owner.calls)
    res = client.post(f"/bff/reviews/a1/actions/{verb}", headers=headers(key=f"r-{verb}"), json={"expected_version": 1, "notes": "rework"})
    assert res.status_code == 410
    assert len(owner.calls) == calls_before
    assert len(store._get_all_commands()) == 0


def test_review_action_routes_forward_votes_and_deny_cross_tenant(review_client, owner):
    client, store = review_client
    calls_before = len(owner.calls)
    res = client.post("/bff/reviews/a1/actions/approve", headers=headers(key="r-approve"), json={"expected_version": 1, "notes": "looks good"})
    assert res.status_code == 202
    assert len(owner.calls) == calls_before + 1
    assert len(store._get_all_commands()) == 0

    cross = client.post("/bff/reviews/b1/actions/approve", headers=headers(key="r-cross"), json={"expected_version": 1, "notes": "cross"})
    assert cross.status_code == 404


@pytest.mark.parametrize("cmd", ["request_revision", "requestrevision", "request_approval_revision", "RequestApprovalRevision"])
def test_direct_revision_commands_return_410(command_client, owner, cmd):
    calls_before = len(owner.calls)
    res = command_client.post("/bff/v1/commands", headers=headers(role="operator", key=f"c-{cmd}"),
                              json={"command": cmd, "target": {"type": "ApprovalDecision", "id": "a1"}, "params": {"decision_id": "a1"}, "audit_context": {"reason": "review"}})
    assert res.status_code == 410
    assert len(owner.calls) == calls_before


@pytest.mark.parametrize("field", ["action", "decision", "action_id"])
def test_wrapped_review_action_revision_returns_410(command_client, owner, field):
    calls_before = len(owner.calls)
    res = command_client.post("/bff/v1/commands", headers=headers(role="operator", key=f"w-{field}"),
                              json={"command": "ReviewAction", "target": {"type": "Review", "id": "a1"}, "params": {"decision_id": "a1", field: "requestrevision", "notes": "rework"}, "audit_context": {"reason": "review"}})
    assert res.status_code == 410
    assert len(owner.calls) == calls_before
