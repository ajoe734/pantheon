"""Retired commands cannot enqueue work or emit manufactured owner results."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.command_adapters.retired import RETIRED_COMMANDS
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import OperatorIdentity


@pytest.mark.parametrize("command", RETIRED_COMMANDS)
def test_retired_mounted_command_has_no_effect(tmp_path, command):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    executed = []

    def identity(auth, **kwargs):
        if auth != "Bearer test":
            raise HTTPException(401)
        return OperatorIdentity(operator_id="operator", roles=["admin"], mfa_verified=True)

    app = FastAPI()
    app.include_router(create_command_adapters_router(service=CommandAdapterService(
        command_store=store, extract_identity=identity,
        process_command_task=executed.append,
    )))
    client = TestClient(app)
    payload = {"command": command, "target": {"type": "Review", "id": "target"}}
    assert client.post("/bff/v1/commands", json=payload).status_code == 401
    for _ in range(2):
        response = client.post("/bff/v1/commands", json=payload, headers={
            "Authorization": "Bearer test", "Idempotency-Key": "same-key",
        })
        assert response.status_code == 410, response.text
        assert RETIRED_COMMANDS[command] in response.text
    assert store.get_command_by_idempotency_key("same-key", operator_id="operator") is None
    assert not executed


@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_internal_rollback_retirement_does_not_record(monkeypatch, verb):
    from types import SimpleNamespace
    from services.control_plane.internal import internal_api
    writes = []
    monkeypatch.setattr(internal_api, "validate_request_auth", lambda **kwargs:
                        SimpleNamespace(actor_id="test-approver", mfa_verified=True, mfa_token="123456"))
    monkeypatch.setattr(internal_api, "_record_command", lambda *args: writes.append(args))
    response = internal_api.app.test_client().post(f"/api/internal/v1/rollbacks/r1/{verb}", json={})
    assert response.status_code == 410
    assert response.json["replacement"] == "/bff/approvals/{decision_id}/decide"
    assert not writes


@pytest.mark.parametrize("owner_response,verified", [
    ({"dispatch_path": "some-owner", "downstream_verified": True}, False),
    ({"dispatch_path": "some-owner", "authoritative_readback": {"plan_id": "p1", "status": "approved"}}, True),
])
def test_execution_verification_requires_owner_response(tmp_path, monkeypatch, owner_response, verified):
    import asyncio
    from services.control_plane.bff import command_executor
    from services.control_plane.bff.command_adapters.service import process_command
    from services.control_plane.bff.models import CommandStatus, CommandType, ObjectType, TargetObject
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    store.submit_command(command_id="cmd", command_type=CommandType.DEPLOYMENT_PATCH,
        target=TargetObject(type=ObjectType.DEPLOYMENT, id="p1"), submitted_at="2026-10-02T00:00:00Z",
        params={}, audit_context={"operator_id": "test"})
    monkeypatch.setattr(command_executor, "execute_command_with_status",
                        lambda *args, **kwargs: (CommandStatus.EXECUTED, owner_response, None))
    asyncio.run(process_command("cmd", command_store=store))
    assert store.get_command("cmd")["audit"]["downstream_verified"] is verified


@pytest.fixture
def default_app(tmp_path):
    from services.control_plane.bff.bootstrap.dependencies import AppDependencies
    from services.control_plane.bff.core.app_factory import compose_bff_app
    from services.control_plane.bff.tests.test_receipt_owner_routes import identity
    deps = AppDependencies.create_default(command_store=CommandStore(str(tmp_path / "default.jsonl")))
    return TestClient(compose_bff_app(app_deps=deps, _extract_identity=identity)), deps


@pytest.mark.parametrize("method,path", [
    ("POST", "/bff/ranking-formulas"),
    ("PATCH", "/bff/ranking-formulas/formula-a"),
    ("POST", "/bff/rankings/ranking-a/actions/publish"),
    ("POST", "/bff/audit/export"),
    ("POST", "/bff/reviews"),
    ("POST", "/bff/reviews/review-a/actions/unsupported"),
])
def test_default_composed_retired_resource_has_no_receipt(default_app, method, path):
    client, deps = default_app
    assert client.request(method, path, json={}).status_code == 401
    headers = {"Authorization": "Bearer tenant-a", "Idempotency-Key": "retired-resource"}
    for _ in range(2):
        response = client.request(method, path, json={}, headers=headers)
        assert response.status_code == 410, response.text
        assert "/bff/" in response.text
    assert deps.command_store.get_command_by_idempotency_key("retired-resource", operator_id="tenant-a") is None
