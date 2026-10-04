"""Mounted adapters execute against an isolated durable owner HTTP boundary."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from functools import partial

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.auth.policy import bff_error, require_operator_role, require_read_role
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
from services.control_plane.bff.deployment.router import create_deployment_router
from services.control_plane.bff.deployment.adapters import DeploymentReadSurfaceAdapter
from services.control_plane.bff.evolution.router import create_evolution_programs_router
from services.control_plane.bff.models import CommandType, ObjectType, OperatorIdentity, utc_now
from services.control_plane.bff.ports import create_read_surface_ports


_JWT_ENV = {"PANTHEON_BFF_JWT_SECRET": "receipt-owner-signing-secret-0123456789",
            "PANTHEON_BFF_JWT_ISSUER": "receipt-owner-tests", "PANTHEON_BFF_JWT_AUDIENCE": "bff-operators",
            "PANTHEON_BFF_AUTH_MODE": "strict"}


def _tok(tenant):
    """Deterministic HS256 token verified by the production identity path; tenant=None omits the claim."""
    from services.runtime_auth_inbound import encode_jwt_hs256
    claims = {"sub": tenant or "no-tenant", "roles": ["operator", "approver"], "exp": 4102444800,
              "iss": _JWT_ENV["PANTHEON_BFF_JWT_ISSUER"], "aud": _JWT_ENV["PANTHEON_BFF_JWT_AUDIENCE"]}
    if tenant:
        claims["tenant_id"] = tenant
    return "Bearer " + encode_jwt_hs256(claims, secret=_JWT_ENV["PANTHEON_BFF_JWT_SECRET"])


def _tenant_of_auth(auth):
    return next((t for t in ("tenant-a", "tenant-b") if auth == _tok(t)), None)


def identity(auth=None, **kwargs):
    from services.control_plane.bff.auth.policy import extract_identity_jwt
    return extract_identity_jwt(auth)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    for key, value in _JWT_ENV.items():
        monkeypatch.setenv(key, value)
    state = tmp_path / "owner.json"
    state.write_text(json.dumps({"plans": {}, "programs": {
        "program-a": {"program_id": "program-a", "status": "active", "tenant_id": "tenant-a"},
    }, "proposals": {"proposal-a": {"decision_id": "proposal-a", "decision_state": "approved", "tenant_id": "tenant-a"}}, "gates": {}, "writes": 0}))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            data = json.loads(state.read_text())
            tenant = _tenant_of_auth(self.headers.get("Authorization", ""))
            if not tenant:
                return self.send_json(401, {})
            path = self.path
            if self.command == "GET":
                if path == "/api/deployment/plans":
                    return self.send_json(200, [r for r in data["plans"].values() if r["tenant_id"] == tenant])
                if path == "/api/evolution/programs":
                    return self.send_json(200, {"items": [r for r in data["programs"].values() if r["tenant_id"] == tenant]})
                if path == "/api/evolution/proposals":
                    return self.send_json(200, [r for r in data["proposals"].values() if r["tenant_id"] == tenant])
                row = data["plans"].get(path.split("/")[-1])
                return self.send_json(200, row) if row and row["tenant_id"] == tenant else self.send_json(404, {})
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if path == "/api/deployment/plans":
                row = {"plan_id": "plan-a", "status": "draft", "tenant_id": tenant}
                data["plans"]["plan-a"] = row
            elif path == "/api/deployment/plans/plan-a/status":
                row = data["plans"]["plan-a"]
                if row["tenant_id"] != tenant:
                    return self.send_json(404, {})
                row["status"] = body["status"]
            elif path == "/api/evolution/proposals/proposal-a/execute":
                row = data["proposals"]["proposal-a"]
                if row["tenant_id"] != tenant:
                    return self.send_json(404, {})
                if body.get("execution_receipt") != {"plane": "research", "record_id": "run-a"}:
                    return self.send_json(422, {})
                row["decision_state"] = "executed"
            elif path == "/api/evolution/programs/program-a/actions/pause_program":
                row = data["programs"]["program-a"]
                if row["tenant_id"] != tenant:
                    return self.send_json(404, {})
                row["status"] = "paused"
                data["writes"] += 1
                state.write_text(json.dumps(data))
                return self.send_json(200, {"receipt_id": "owner-receipt", "program_id": "program-a",
                    "action_id": "pause_program", "status": "paused", "program_status": "paused", "program": row})
            else:
                return self.send_json(404, {})
            data["writes"] += 1
            state.write_text(json.dumps(data))
            self.send_json(200, row)

        def send_json(self, status, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = handle_request
        do_POST = handle_request

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for env in ("PANTHEON_DEPLOYMENT_API_URL", "PANTHEON_EVOLUTION_API_URL"):
        monkeypatch.setenv(env, f"http://127.0.0.1:{server.server_port}")
    yield state
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


@pytest.fixture
def mounted(owner, tmp_path):
    from services.control_plane.bff.bootstrap.dependencies import AppDependencies
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    ports = AppDependencies.create_default(command_store=store).read_surface
    svc = CommandAdapterService(command_store=store, read_surface=ports, extract_identity=identity)
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    common = dict(extract_identity=identity, require_read_role=require_read_role,
                  require_operator_role=require_operator_role, bff_error=bff_error, utc_now=utc_now)
    app.include_router(create_deployment_router(
        queries=DeploymentReadSurfaceAdapter(read_surface=ports), **common,
        page_slice=lambda rows, token, size: (rows[:size], None),
        snapshot_meta=lambda at: {"snapshot_at": at},
        dataset_surface_status=lambda *args, **kwargs: {"status": "ok"},
        composed_surface_status=lambda **kwargs: {}, read_surface_meta=lambda *args, **kwargs: {},
        raise_if_read_surface_unavailable=lambda *args, **kwargs: None,
        aggregate_group_surface=lambda *args, **kwargs: {}, split_csv_query=lambda value: [],
        meta_staleness=lambda: None, stable_json_hash=lambda body: "unused",
        resolve_final_idempotency_key=lambda key, other: key or other,
        reject_body_idempotency_key=lambda payload: None, request_dry_run_requested=lambda *args: False,
        gov_bff_idempotency={}, publish_event=lambda *args: None, sse_buffers={}, sse_subscribers={},
        gov_bff_action_command=svc.submit_resource_action,
        deprecated_bff_path_response=lambda **kwargs: (_ for _ in ()).throw(HTTPException(410)),
        sem_command_response=svc.sem_command_response, stream_generic_events=lambda *args: None,
        surface_degradation_reason=lambda *args: None,
    ))
    app.include_router(create_evolution_programs_router(
        read_surface=ports, **common,
        submit_program_action=lambda entity_type, entity_id, action_id, key, ident, payload, **ctx:
            svc.submit_resource_action(ObjectType.EVOLUTION_PROGRAM, entity_id, action_id, key,
                                       ident, payload, CommandType.EVOLUTION_PROGRAM_ACTION, **ctx),
    ))
    from services.control_plane.bff.command_adapters.router import create_command_adapters_router
    app.include_router(create_command_adapters_router(service=svc))
    return TestClient(app), store, ports


@pytest.mark.parametrize("create_path", ["/bff/deployments", "/api/v1/deployment-plans"])
def test_deployment_owner_effect_and_default_refresh(mounted, owner, create_path):
    client, store, ports = mounted
    headers = {"Authorization": _tok("tenant-a"), "Idempotency-Key": "create"}
    assert client.post(create_path, json={}).status_code == 401
    first = client.post(create_path, json={"reason": "create paper plan"}, headers=headers)
    assert first.status_code == 202, first.text
    command = store.get_command_by_idempotency_key("create", operator_id="tenant-a")
    assert command["status"] == "executed", command
    assert client.post(create_path, json={"reason": "create paper plan"}, headers=headers).status_code == 202
    assert json.loads(owner.read_text())["writes"] == 1
    read = client.get("/bff/deployments", headers=headers)
    assert read.json()["data"][0]["plan_id"] == "plan-a", read.text
    assert client.get("/bff/deployments", headers={"Authorization": _tok("tenant-b")}).json()["data"] == []
    patched = client.patch("/bff/deployments/plan-a", json={"status": "approved"},
                           headers={**headers, "Idempotency-Key": "patch"})
    assert patched.status_code == 202, patched.text
    assert store.get_command_by_idempotency_key("patch", operator_id="tenant-a")["status"] == "executed"
    assert client.get("/bff/deployments", headers=headers).json()["data"][0]["status"] == "approved"
    assert json.loads(owner.read_text())["writes"] == 2


def test_program_owner_effect_and_default_refresh(mounted, owner):
    client, store, ports = mounted
    url = "/bff/evolution-programs/program-a/actions/pause_program"
    headers = {"Authorization": _tok("tenant-a"), "Idempotency-Key": "pause"}
    response = client.post(url, json={}, headers=headers)
    assert response.status_code == 202, response.text
    record = store.get_command_by_idempotency_key("pause", operator_id="tenant-a")
    assert record["status"] == "executed", record
    assert client.get("/bff/evolution-programs/program-a", headers=headers).json()["data"]["status"] == "paused"
    assert client.post(url, json={}, headers=headers).status_code == 202
    assert json.loads(owner.read_text())["writes"] == 1
    assert client.post(url, json={}, headers={**headers, "Authorization": _tok("tenant-b")}).status_code == 404


def test_proposal_execute_preserves_owner_receipt_and_replays(mounted, owner, monkeypatch):
    client, store, ports = mounted
    monkeypatch.setenv("PANTHEON_GOVERNANCE_API_URL", "http://must-not-route-to-governance.invalid")
    headers = {"Authorization": _tok("tenant-a"), "Idempotency-Key": "proposal-execute"}
    payload = {"command": "ExecuteEvolutionAction", "target": {"type": "EvolutionDecision", "id": "proposal-a"},
               "params": {"action_type": "retrain", "evolution_decision_id": "proposal-a", "execution_receipt": {"plane": "research", "record_id": "run-a"}},
               "audit_context": {"reason": "Owner verifies execution evidence"}}
    result = client.post("/bff/v1/commands", json=payload, headers=headers)
    assert result.status_code == 202, result.text
    record = store.get_command_by_idempotency_key("proposal-execute", operator_id="tenant-a")
    assert record["status"] == "executed", record
    assert record["audit"]["downstream_verified"] is True
    assert json.loads(owner.read_text())["proposals"]["proposal-a"]["decision_state"] == "executed"
    assert client.post("/bff/v1/commands", json=payload, headers=headers).status_code == 202
    assert json.loads(owner.read_text())["writes"] == 1


def test_same_operator_cannot_replay_another_tenant_receipt(owner, tmp_path):
    from services.control_plane.bff.command_adapters.router import create_command_adapters_router
    def shared_operator(auth, **kwargs):
        result = identity(auth)
        result.operator_id = "shared-operator"
        return result
    store = CommandStore(str(tmp_path / "tenant-commands.jsonl"))
    app = FastAPI()
    app.include_router(create_command_adapters_router(service=CommandAdapterService(
        command_store=store, extract_identity=shared_operator,
    )))
    client = TestClient(app)
    payload = {"command": "CreateDeployment", "target": {"type": "Deployment", "id": "plan-a"},
               "params": {}, "audit_context": {"reason": "tenant scoped admission"}}
    headers = {"Authorization": _tok("tenant-a"), "Idempotency-Key": "shared-key"}
    assert client.post("/bff/v1/commands", json=payload, headers=headers).status_code == 202
    other = client.post("/bff/v1/commands", json=payload, headers={**headers, "Authorization": _tok("tenant-b")})
    assert other.status_code == 409, other.text
    assert json.loads(owner.read_text())["writes"] == 1


def test_default_composition_forwards_validated_browser_session(owner, tmp_path):
    from services.control_plane.bff.bootstrap.dependencies import AppDependencies
    from services.control_plane.bff.core.app_factory import compose_bff_app
    deps = AppDependencies.create_default(command_store=CommandStore(str(tmp_path / "cookie-commands.jsonl")))
    app = compose_bff_app(app_deps=deps, _extract_identity=identity, dev_login_enabled=lambda: True,
                          validate_session=lambda token: identity("Bearer " + token), origin_allowed=lambda origin: False)
    client = TestClient(app)
    client.cookies.set("pantheon_session", _tok("tenant-a").removeprefix("Bearer "))
    result = client.get("/bff/evolution-programs/program-a")
    assert result.status_code == 200, result.text
    assert result.json()["data"]["program_id"] == "program-a"
    client.cookies.set("pantheon_session", "invalid")
    assert client.get("/bff/evolution-programs/program-a").status_code == 401


@pytest.mark.parametrize("method,path,payload,extra_headers", [
    ("POST", "/api/v1/deployment-plans", {}, {"X-Dry-Run": "true"}),
    ("POST", "/bff/deployments", {"dryRun": True}, {}),
    ("PATCH", "/bff/deployments/plan-a", {"dryRun": True, "status": "approved"}, {}),
])
def test_resource_dry_run_never_enqueues_or_writes_owner(mounted, owner, method, path, payload, extra_headers):
    client, store, ports = mounted
    response = client.request(method, path, json=payload, headers={
        "Authorization": _tok("tenant-a"), "Idempotency-Key": "dry-run", **extra_headers,
    })
    assert response.status_code == 202, response.text
    assert response.json()["meta"]["dryRun"] is True
    assert store.get_command_by_idempotency_key("dry-run", operator_id="tenant-a") is None
    assert json.loads(owner.read_text())["writes"] == 0


@pytest.mark.parametrize("action", ["promote_candidate_live", "PromoteEvolutionCandidateLive"])
def test_live_candidate_promotion_requires_two_man_evidence_at_mounted_path(mounted, owner, action):
    client, store, ports = mounted
    url = f"/bff/evolution-programs/program-a/actions/{action}"
    headers = {"Authorization": _tok("tenant-a"), "Idempotency-Key": f"promote-live-{action}"}
    evidence = {"candidate_id": "cand-a", "confirmToken": "ct-1", "approvalId": "appr-1"}
    response = client.post(url, json=evidence, headers=headers)
    assert response.status_code == 409, response.text
    assert "TWO_MAN_SIGNATURE_REQUIRED" in response.text
    assert store.get_command_by_idempotency_key(f"promote-live-{action}", operator_id="tenant-a") is None
    assert json.loads(owner.read_text())["writes"] == 0
