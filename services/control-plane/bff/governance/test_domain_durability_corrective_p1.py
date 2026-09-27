from types import SimpleNamespace
from unittest.mock import patch
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.governance.service import GovernanceService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.capital.service import DefaultCapitalAuthority
from services.control_plane.bff.runtime.router import create_runtime_router

PAYLOAD = {"plan_id": "review-plan", "decision": "approve", "memo": "isolated reviewer check"}
IDENTITY = SimpleNamespace(operator_id="reviewer-test", roles=["admin", "approver", "operator"])


def governance_client(store, identity=IDENTITY):
    service = GovernanceService(object(), command_store=store)
    app = FastAPI()
    app.include_router(create_governance_router(
        governance_service=service, command_store=store,
        submit_action=lambda **kw: None,
        extract_identity=lambda authorization: identity,
    ))
    return TestClient(app, raise_server_exceptions=False), service


def test_approval_storage_failure_must_not_be_accepted(tmp_path):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    client, service = governance_client(store)
    with patch.object(store, "submit_terminal_command", side_effect=OSError("injected disk failure")):
        response = client.post("/api/v1/approval-decisions", json=PAYLOAD, headers={"Idempotency-Key": "key"})
    assert response.status_code >= 500, (response.status_code, response.json(), store._get_all_commands())


def test_unconfigured_approval_owner_must_not_accept():
    client, service = governance_client(None)
    response = client.post("/api/v1/approval-decisions", json=PAYLOAD, headers={"Idempotency-Key": "key"})
    fresh = GovernanceService(object())
    assert response.status_code == 503, (response.status_code, service.list_approval_decisions(), fresh.list_approval_decisions())


def test_approval_actor_scoped_replay(tmp_path):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    identity = SimpleNamespace(operator_id="actor-a", roles=["admin"])
    client, service = governance_client(store, identity)
    first = client.post("/api/v1/approval-decisions", json=PAYLOAD, headers={"Idempotency-Key": "shared-key"})
    identity.operator_id = "actor-b"
    second = client.post("/api/v1/approval-decisions", json=PAYLOAD, headers={"Idempotency-Key": "shared-key"})
    assert first.status_code == second.status_code == 202
    assert second.json()["data"]["approver_id"] == "actor-b", second.json()


def test_approval_persists_with_configured_store(tmp_path):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    client, service = governance_client(store)
    response = client.post("/api/v1/approval-decisions", json=PAYLOAD, headers={"Idempotency-Key": "key"})
    assert response.status_code == 202
    assert len(store._get_all_commands()) == 1, store._get_all_commands()


def test_capital_approval_must_not_execute_apply():
    read = SimpleNamespace(get_rebalance=lambda rid: {"id": rid, "status": "proposed"})
    app = FastAPI()
    app.include_router(create_capital_router(
        get_read_store=lambda: read,
        extract_identity=lambda authorization: IDENTITY,
        require_operator_role=lambda identity: None,
    ))
    client = TestClient(app, raise_server_exceptions=True)
    with patch("services.control_plane.bff.command_adapters.capital_adapter.capital_url", side_effect=lambda path: "http://isolated.invalid" + path), patch("services.control_plane.bff.command_adapters.capital_adapter.http_request_json", return_value={"rebalance_id": "r1", "status": "applied"}) as http:
        response = client.post("/bff/rebalances/r1/approve", json={"memo": "isolated approval"}, headers={"Idempotency-Key": "key"})
    calls = [(call.args[0], call.kwargs.get("method")) for call in http.call_args_list]
    assert response.status_code < 400, (response.status_code, response.json(), calls)
    assert not any(url.endswith("/apply") and method == "POST" for url, method in calls), (response.status_code, calls)


def test_capital_sign_must_not_execute_apply():
    read = SimpleNamespace(get_rebalance=lambda rid: {"id": rid, "status": "approved"})
    app = FastAPI()
    app.include_router(create_capital_router(
        get_read_store=lambda: read,
        extract_identity=lambda authorization: IDENTITY,
        require_operator_role=lambda identity: None,
    ))
    client = TestClient(app, raise_server_exceptions=True)
    with patch("services.control_plane.bff.command_adapters.capital_adapter.capital_url", side_effect=lambda path: "http://isolated.invalid" + path), patch("services.control_plane.bff.command_adapters.capital_adapter.http_request_json", return_value={"rebalance_id": "r1", "status": "signed"}) as http:
        response = client.post("/bff/rebalances/r1/two-man-sign", json={"signature": "isolated-sig"}, headers={"Idempotency-Key": "key-sign"})
    calls = [(call.args[0], call.kwargs.get("method")) for call in http.call_args_list]
    assert response.status_code < 400, (response.status_code, response.json(), calls)
    assert not any(url.endswith("/apply") and method == "POST" for url, method in calls), (response.status_code, calls)


def test_capital_authority_composes_command_store(tmp_path):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    auth = DefaultCapitalAuthority(command_store=store)
    with patch("services.control_plane.bff.command_adapters.capital_adapter.capital_url", side_effect=lambda path: "http://isolated.invalid" + path), patch("services.control_plane.bff.command_adapters.capital_adapter.http_request_json", return_value={"rebalance_id": "r1", "status": "approved"}):
        result = auth.approve_rebalance({"memo": "testing store"}, "r1", actor_id="operator-1")
    assert result is not None
    commands = store._get_all_commands()
    assert len(commands) == 1
    assert commands[0]["type"] == "ApproveRebalance"
    assert commands[0]["target"]["id"] == "r1"


def test_runtime_router_unconfigured_fails_closed():
    def mock_bff_error(status_code, code, message, reason=None, **details):
        from fastapi import HTTPException
        return HTTPException(status_code=status_code, detail={"error": {"code": str(code), "message": message, "reason": reason or message, **details}})

    read = SimpleNamespace(list_runtime_bindings=lambda: [])
    app = FastAPI()
    app.include_router(create_runtime_router(
        read_surface=read,
        dependencies={
            "_extract_identity": lambda auth: IDENTITY,
            "_require_operator_role": lambda ident: None,
            "_resolve_final_idempotency_key": lambda k, d=None: k or d or "key",
            "_reject_body_idempotency_key": lambda p: None,
            "_dataset_surface_status": lambda *a, **k: {},
            "_snapshot_meta": lambda *a, **k: {},
            "_bff_error": mock_bff_error,
            "_stable_json_hash": lambda v: "stable-hash",
            "_request_dry_run_requested": lambda: False,
            "_GOV_BFF_IDEMPOTENCY": {},
            "utc_now": lambda: "2026-09-27T00:00:00Z",
            "runtime_owner_port": None,
        }
    ))
    client = TestClient(app, raise_server_exceptions=False)
    payload = {
        "deployment_plan_id": "dp-1",
        "binding_id": "b-1",
        "name": "runtime-1",
        "persona_id": "p-1",
        "runtime_kind": "paper",
    }
    response = client.post("/bff/runtimes", json=payload, headers={"Idempotency-Key": "rt-key", "X-Dry-Run": "0"})
    assert response.status_code == 503, (response.status_code, response.json())
