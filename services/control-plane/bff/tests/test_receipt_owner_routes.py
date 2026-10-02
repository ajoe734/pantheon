"""Mounted adapters execute against an isolated durable owner HTTP boundary."""
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


def identity(auth=None, **kwargs):
    if auth not in {"Bearer tenant-a", "Bearer tenant-b"}:
        raise HTTPException(401)
    tenant = auth.split()[1]
    return OperatorIdentity(operator_id=tenant, roles=["operator", "approver"],
                            claims={"tenant_id": tenant}, mfa_verified=True)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    state = tmp_path / "owner.json"
    state.write_text(json.dumps({"plans": {}, "programs": {
        "program-a": {"program_id": "program-a", "status": "active", "tenant_id": "tenant-a"},
    }, "writes": 0}))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            data = json.loads(state.read_text())
            tenant = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if tenant not in {"tenant-a", "tenant-b"}:
                return self.send_json(401, {})
            path = self.path
            if self.command == "GET":
                if path == "/api/deployment/plans":
                    return self.send_json(200, [r for r in data["plans"].values() if r["tenant_id"] == tenant])
                if path == "/api/evolution/programs":
                    return self.send_json(200, {"items": [r for r in data["programs"].values() if r["tenant_id"] == tenant]})
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
    ports = create_read_surface_ports()
    store = CommandStore(str(tmp_path / "commands.jsonl"))
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
    return TestClient(app), store, ports


@pytest.mark.parametrize("create_path", ["/bff/deployments", "/api/v1/deployment-plans"])
def test_deployment_owner_effect_and_default_refresh(mounted, owner, create_path):
    client, store, ports = mounted
    headers = {"Authorization": "Bearer tenant-a", "Idempotency-Key": "create"}
    assert client.post(create_path, json={}).status_code == 401
    first = client.post(create_path, json={"reason": "create paper plan"}, headers=headers)
    assert first.status_code == 202, first.text
    command = store.get_command_by_idempotency_key("create", operator_id="tenant-a")
    assert command["status"] == "executed", command
    assert client.post(create_path, json={"reason": "create paper plan"}, headers=headers).status_code == 202
    assert json.loads(owner.read_text())["writes"] == 1
    read = client.get("/bff/deployments", headers=headers)
    assert read.json()["data"][0]["plan_id"] == "plan-a", read.text
    assert client.get("/bff/deployments", headers={"Authorization": "Bearer tenant-b"}).json()["data"] == []
    patched = client.patch("/bff/deployments/plan-a", json={"status": "approved"},
                           headers={**headers, "Idempotency-Key": "patch"})
    assert patched.status_code == 202, patched.text
    assert store.get_command_by_idempotency_key("patch", operator_id="tenant-a")["status"] == "executed"
    assert client.get("/bff/deployments", headers=headers).json()["data"][0]["status"] == "approved"
    assert json.loads(owner.read_text())["writes"] == 2


def test_program_owner_effect_and_default_refresh(mounted, owner):
    client, store, ports = mounted
    url = "/bff/evolution-programs/program-a/actions/pause_program"
    headers = {"Authorization": "Bearer tenant-a", "Idempotency-Key": "pause"}
    response = client.post(url, json={}, headers=headers)
    assert response.status_code == 202, response.text
    record = store.get_command_by_idempotency_key("pause", operator_id="tenant-a")
    assert record["status"] == "executed", record
    assert client.get("/bff/evolution-programs/program-a", headers=headers).json()["data"]["status"] == "paused"
    assert client.post(url, json={}, headers=headers).status_code == 202
    assert json.loads(owner.read_text())["writes"] == 1
    assert client.post(url, json={}, headers={**headers, "Authorization": "Bearer tenant-b"}).status_code == 404
