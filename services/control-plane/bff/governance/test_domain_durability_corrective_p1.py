from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from unittest.mock import patch
import inspect
import json
import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.governance.service import GovernanceService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandStatus
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.capital.service import DefaultCapitalAuthority
from services.control_plane.bff.runtime.router import create_runtime_router
from services.control_plane.bff.runtime.service import _resolve_default_runtime_owner_port
from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_adapters.router import create_command_adapters_router
from services.control_plane.bff.deployment.router import create_deployment_router
from services.control_plane.bff.research.router import create_research_experiments_router, create_research_router
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from services.foundation.postgres_json_store import PostgresJsonOwnerStore
from services.research.write_owner import ResearchWriteOwner

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


def capital_client(store):
    app = FastAPI()
    app.include_router(create_capital_router(
        read_surface=SimpleNamespace(get_rebalance=lambda rid: {"id": rid, "status": "proposed"}),
        command_store=store,
        extract_identity=lambda auth: SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["admin"]),
        require_operator_role=lambda identity: None,
    ))
    return TestClient(app, raise_server_exceptions=False)


def test_capital_restart_replay(tmp_path):
    path = str(tmp_path / "commands.jsonl")
    store = CommandStore(path)
    with patch("services.control_plane.bff.command_adapters.capital_adapter.capital_url", lambda p: "http://isolated.invalid" + p), patch("services.control_plane.bff.command_adapters.capital_adapter.http_request_json", return_value={"rebalance_id": "r1", "status": "approved"}) as http:
        first = capital_client(store).post("/bff/rebalances/r1/approve", json={"memo": "review memo"}, headers={"Idempotency-Key": "retry-key"})
        second = capital_client(CommandStore(path)).post("/bff/rebalances/r1/approve", json={"memo": "review memo"}, headers={"Idempotency-Key": "retry-key"})
    posts = [c for c in http.call_args_list if c.kwargs.get("method") == "POST"]
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert len(posts) == 1, {"posts": len(posts), "rows": len(store._get_all_commands())}


def test_capital_storage_failure_prevents_dispatch(tmp_path):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    with patch("services.control_plane.bff.command_adapters.capital_adapter.capital_url", lambda p: "http://isolated.invalid" + p), patch.object(store, "submit_command", side_effect=OSError("disk unavailable")), patch("services.control_plane.bff.command_adapters.capital_adapter.http_request_json", return_value={"rebalance_id": "r1", "status": "approved"}) as http:
        response = capital_client(store).post("/bff/rebalances/r1/approve", json={"memo": "review memo"}, headers={"Idempotency-Key": "retry-key"})
    assert response.status_code >= 500
    posts = [c for c in http.call_args_list if c.kwargs.get("method") == "POST"]
    assert not posts, {"downstream_posts_before_storage_failure": len(posts), "durable_rows": len(store._get_all_commands())}


@pytest.mark.parametrize("changed_scope", ["actor", "tenant"])
def test_governance_scoped_replay(tmp_path, changed_scope):
    from services.control_plane.bff.command_adapters.service import CommandAdapterService

    store = CommandStore(str(tmp_path / "commands.jsonl"))
    identity = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["admin"])
    service = CommandAdapterService(command_store=store, check_read_surface_state=lambda: None)
    app = FastAPI()
    app.include_router(create_governance_router(command_store=store, submit_action=service.submit_governance_action, extract_identity=lambda auth: identity))
    client = TestClient(app)
    payload = {"review_id": "review-1"}
    first = client.post("/bff/reviews", json=payload, headers={"Idempotency-Key": "shared-key"})
    if changed_scope == "actor":
        identity.operator_id = "actor-b"
    else:
        identity.tenant_id = "tenant-b"
    second = client.post("/bff/reviews", json=payload, headers={"Idempotency-Key": "shared-key"})
    assert first.status_code == second.status_code == 202, (first.text, second.text)
    assert len(store._get_all_commands()) == 2, {"scope": changed_scope, "rows": len(store._get_all_commands())}


def test_sponsor_adapter_preserves_required_rationale():
    from services.control_plane.bff.command_adapters.governance_adapter import GovernanceCommandAdapter

    with patch("services.control_plane.bff.command_adapters.governance_adapter.internal_url", lambda p: "http://isolated.invalid" + p), patch("services.control_plane.bff.command_adapters.governance_adapter.http_request_json", return_value={"sponsor_decision": "rejected", "committee_id": "c1"}) as http:
        receipt = GovernanceCommandAdapter().execute("cmd-1", "RecordSponsorDecision", {"committee_id": "c1", "sponsor_decision": "rejected", "rationale_ref": "evidence://rationale", "actor_id": "actor-a"})
    payload = http.call_args.kwargs["payload"]
    assert payload.get("rationale_ref") == "evidence://rationale", {"outbound": payload, "receipt": receipt}
    assert receipt["status"] == "rejected"
    assert receipt["authoritative_readback"]["status"] == "rejected"


def test_runtime_router_resolves_runtime_owner_port(tmp_path, monkeypatch):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path))
    recorded = []
    mock_port = SimpleNamespace(deploy=lambda req: (recorded.append(req), {"runtime_id": req["runtime_id"], "status": "running", **req})[1])
    app = FastAPI()
    app.include_router(create_runtime_router(
        read_surface=SimpleNamespace(list_runtime_bindings=lambda: []),
        runtime_owner_port=mock_port,
        dependencies={
            "_extract_identity": lambda auth: IDENTITY,
            "_require_operator_role": lambda ident: None,
            "_resolve_final_idempotency_key": lambda k, d=None: k or d or "key",
            "_reject_body_idempotency_key": lambda p: None,
            "_dataset_surface_status": lambda *a, **k: {},
            "_snapshot_meta": lambda *a, **k: {},
            "_bff_error": lambda s, c, m, r=None: Exception(f"{s}: {m}"),
            "_stable_json_hash": lambda v: "stable-hash",
            "_request_dry_run_requested": lambda: False,
            "_GOV_BFF_IDEMPOTENCY": {},
            "_sse_buffers": {"runtime": []},
            "_sse_subscribers": {"runtime": []},
            "_publish_event": lambda *a, **k: None,
            "utc_now": lambda: "2026-09-27T00:00:00Z",
        },
    ))
    client = TestClient(app, raise_server_exceptions=True)
    payload = {
        "deployment_plan_id": "dp-1",
        "binding_id": "b-1",
        "name": "runtime-mock",
        "persona_id": "p-1",
        "runtime_kind": "paper",
    }
    response = client.post("/bff/runtimes", json=payload, headers={"Idempotency-Key": "rt-key", "X-Dry-Run": "0"})
    assert response.status_code == 201
    assert len(recorded) == 1
    assert response.json()["data"]["id"] == recorded[0]["runtime_id"]


RUNTIME_REG_PAYLOAD = {'deployment_plan_id': 'dp-1', 'binding_id': 'b-1', 'name': 'runtime-test', 'persona_id': 'p-1', 'runtime_kind': 'paper'}
RUNTIME_REG_HEADERS = {'Idempotency-Key': 'review-key'}
RUNTIME_REG_IDENTITY = SimpleNamespace(operator_id='actor-a', tenant_id='tenant-a', roles=['admin'])


def _isolated_runtime_client(dispatches, *, date='2026-09-27T00:00:00Z', owner=True):
    deps = {
        '_extract_identity': lambda auth: RUNTIME_REG_IDENTITY,
        '_require_operator_role': lambda identity: None,
        '_resolve_final_idempotency_key': lambda k, d=None: k or d,
        '_reject_body_idempotency_key': lambda p: None,
        '_dataset_surface_status': lambda *a, **k: {},
        '_snapshot_meta': lambda *a, **k: {},
        '_bff_error': lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
        '_stable_json_hash': lambda p: str(p),
        '_request_dry_run_requested': lambda: False,
        '_GOV_BFF_IDEMPOTENCY': {},
        '_sse_buffers': {'runtime': []},
        '_sse_subscribers': {'runtime': []},
        '_publish_event': lambda *a, **k: None,
        'utc_now': lambda: date,
    }
    port = SimpleNamespace(deploy=lambda req: (dispatches.append(req), dict(req, status='running'))[1]) if owner else None
    app = FastAPI()
    app.include_router(create_runtime_router(read_surface=SimpleNamespace(list_runtime_bindings=lambda: []), runtime_owner_port=port, dependencies=deps))
    return TestClient(app)


def test_runtime_default_owner_resolves_without_nameerror(monkeypatch):
    monkeypatch.delenv('PANTHEON_RUNTIME_MANAGER_URL', raising=False)
    assert _resolve_default_runtime_owner_port() is None


def test_runtime_restart_preserves_request_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path))
    calls = []
    first = _isolated_runtime_client(calls).post('/bff/runtimes', json=RUNTIME_REG_PAYLOAD, headers=RUNTIME_REG_HEADERS)
    second = _isolated_runtime_client(calls).post('/bff/runtimes', json={**RUNTIME_REG_PAYLOAD, 'name': 'changed'}, headers=RUNTIME_REG_HEADERS)
    rows = CommandStore(str(tmp_path / 'commands.jsonl'))._get_all_commands()
    assert first.status_code == 201
    assert second.status_code == 409, {'second': second.json(), 'dispatches': len(calls), 'durable_commands': rows}


def test_runtime_store_failure_prevents_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path))
    calls = []
    with patch('services.control_plane.bff.command_queue.CommandStore', side_effect=OSError('isolated admission storage failure')):
        response = _isolated_runtime_client(calls).post('/bff/runtimes', json=RUNTIME_REG_PAYLOAD, headers=RUNTIME_REG_HEADERS)
    assert response.status_code >= 500 and not calls, {'status': response.status_code, 'dispatches': calls}


def test_runtime_authenticated_identity_cannot_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path))
    calls = []
    response = _isolated_runtime_client(calls).post('/bff/runtimes', json={**RUNTIME_REG_PAYLOAD, 'params': {'tenant_id': 'forged-tenant', 'actor_id': 'forged-actor', 'idempotency_key': 'forged-key', 'runtime_id': 'forged-runtime'}}, headers=RUNTIME_REG_HEADERS)
    assert response.status_code < 400
    assert calls[0]['tenant_id'] == 'tenant-a' and calls[0]['actor_id'] == 'actor-a' and calls[0]['idempotency_key'] == 'review-key', calls


def test_runtime_cross_day_restart_keeps_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path))
    calls = []
    responses = []
    for date in ('2026-09-27T23:59:59Z', '2026-09-28T00:00:01Z'):
        response = _isolated_runtime_client(calls, date=date).post('/bff/runtimes', json=RUNTIME_REG_PAYLOAD, headers=RUNTIME_REG_HEADERS)
        assert response.status_code == 201
        responses.append(response)
    assert len(calls) == 1, "Restart replay must not redispatch to owner port"
    assert responses[0].json()["data"]["id"] == responses[1].json()["data"]["id"]


def test_capital_pool_create_replays_original_request(tmp_path, monkeypatch):
    monkeypatch.setenv('PANTHEON_CAPITAL_API_URL', 'http://isolated.invalid')
    monkeypatch.setattr('services.control_plane.bff.command_adapters.capital_adapter.capital_url', lambda p: 'http://isolated.invalid' + p)
    path = str(tmp_path / 'commands.jsonl')
    with patch('services.control_plane.bff.command_executor._post_json', side_effect=lambda url, payload: dict(payload)) as http:
        first = capital_client(CommandStore(path)).post('/bff/capital-pools', json={'name': 'isolated-review-pool'}, headers=RUNTIME_REG_HEADERS)
        second = capital_client(CommandStore(path)).post('/bff/capital-pools', json={'name': 'isolated-review-pool'}, headers=RUNTIME_REG_HEADERS)
    assert first.status_code == 201, first.text
    assert second.status_code == 201 and http.call_count == 1, {'second': second.json(), 'posts': http.call_count}


def test_capital_concurrent_admission_keeps_one_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('PANTHEON_CAPITAL_API_URL', 'http://isolated.invalid')
    monkeypatch.setattr('services.control_plane.bff.command_adapters.capital_adapter.capital_url', lambda p: 'http://isolated.invalid' + p)
    path = str(tmp_path / 'commands.jsonl')
    clients = [capital_client(CommandStore(path)) for _ in range(2)]
    barrier = Barrier(2)
    original_submit = CommandStore.submit_command

    def synchronized_submit(self, *args, **kwargs):
        barrier.wait(timeout=10)
        return original_submit(self, *args, **kwargs)

    with patch.object(CommandStore, 'submit_command', synchronized_submit), patch('services.control_plane.bff.command_adapters.capital_adapter.http_request_json', return_value={'rebalance_id': 'r1', 'status': 'approved'}) as http:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda client: client.post('/bff/rebalances/r1/approve', json={'memo': 'isolated'}, headers=RUNTIME_REG_HEADERS), clients))
    posts = [call for call in http.call_args_list if call.kwargs.get('method') == 'POST']
    ids = [call.kwargs['payload']['command_id'] for call in posts]
    assert len(set(ids)) == 1, {'statuses': [r.status_code for r in responses], 'command_ids': ids, 'rows': len(CommandStore(path)._get_all_commands())}


def test_runtime_without_store_must_fail_closed(monkeypatch):
    monkeypatch.delenv('BFF_DATA_DIR', raising=False)
    calls = []
    response = _isolated_runtime_client(calls).post('/bff/runtimes', json=RUNTIME_REG_PAYLOAD, headers=RUNTIME_REG_HEADERS)
    assert response.status_code >= 500 and not calls, {'status': response.status_code, 'dispatches': calls}


def test_runtime_success_persists_terminal_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path))
    calls = []
    response = _isolated_runtime_client(calls).post('/bff/runtimes', json=RUNTIME_REG_PAYLOAD, headers=RUNTIME_REG_HEADERS)
    assert response.status_code == 201
    rows = CommandStore(str(tmp_path / 'commands.jsonl'))._get_all_commands()
    assert len(rows) == 1
    assert rows[0]['status'] == 'executed' and rows[0]['result'], rows[0]


def test_runtime_concurrent_different_payload_must_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path))
    calls = []
    clients = [_isolated_runtime_client(calls) for _ in range(2)]
    barrier = Barrier(2)
    original = CommandStore.submit_command

    def synchronized(self, *args, **kwargs):
        barrier.wait(timeout=10)
        return original(self, *args, **kwargs)

    with patch.object(CommandStore, 'submit_command', synchronized):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda i: clients[i].post('/bff/runtimes', json={**RUNTIME_REG_PAYLOAD, 'name': f'name-{i}'}, headers=RUNTIME_REG_HEADERS), range(2)))
    assert sorted(r.status_code for r in responses) == [201, 409] and len(calls) == 1, {'statuses': [r.status_code for r in responses], 'dispatch_names': [x['name'] for x in calls], 'rows': len(CommandStore(str(tmp_path / 'commands.jsonl'))._get_all_commands())}


@pytest.mark.parametrize('paths', [('/bff/rebalances/r1/approve', '/bff/rebalances/r2/approve'), ('/bff/rebalances/r1/approve', '/bff/rebalances/r1/two-man-sign')])
def test_capital_concurrent_key_must_bind_target_and_operation(tmp_path, monkeypatch, paths):
    monkeypatch.setenv('PANTHEON_CAPITAL_API_URL', 'http://isolated.invalid')
    monkeypatch.setattr('services.control_plane.bff.command_adapters.capital_adapter.capital_url', lambda p: 'http://isolated.invalid' + p)
    path = str(tmp_path / 'commands.jsonl')
    clients = [capital_client(CommandStore(path)) for _ in range(2)]
    barrier = Barrier(2)
    dispatch_barrier = Barrier(2)
    original = CommandStore.submit_command

    def synchronized(self, *args, **kwargs):
        barrier.wait(timeout=10)
        return original(self, *args, **kwargs)

    def http_response(url, **kwargs):
        if kwargs.get('method') == 'POST':
            dispatch_barrier.wait(timeout=2)
        return {'rebalance_id': 'r1', 'status': 'approved'}

    with patch.object(CommandStore, 'submit_command', synchronized), patch('services.control_plane.bff.command_adapters.capital_adapter.http_request_json', side_effect=http_response) as http:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda i: clients[i].post(paths[i], json={'memo': 'same'}, headers=RUNTIME_REG_HEADERS), range(2)))
    posts = [c for c in http.call_args_list if c.kwargs.get('method') == 'POST']
    assert len(posts) == 1 and any(r.status_code == 409 for r in responses), {'statuses': [r.status_code for r in responses], 'urls': [c.args[0] for c in posts], 'ids': [c.kwargs['payload']['command_id'] for c in posts]}


def test_governance_concurrent_replay_keeps_single_durable_decision(tmp_path):
    path = str(tmp_path / 'commands.jsonl')
    clients = [governance_client(CommandStore(path))[0] for _ in range(2)]
    barrier = Barrier(2)
    original = CommandStore.submit_terminal_command

    def synchronized(self, *args, **kwargs):
        barrier.wait(timeout=10)
        return original(self, *args, **kwargs)

    with patch.object(CommandStore, 'submit_terminal_command', synchronized):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda client: client.post('/api/v1/approval-decisions', json={'plan_id': 'review-plan', 'decision': 'approve', 'memo': 'isolated reviewer check'}, headers={'Idempotency-Key': 'review-key'}), clients))
    rows = CommandStore(path)._get_all_commands()
    assert len(rows) == 1, {'statuses': [r.status_code for r in responses], 'ids': [r['command_id'] for r in rows]}


REVIEW_IDENTITY = SimpleNamespace(operator_id='review-actor', tenant_id='review-tenant', roles=['admin'])
REVIEW_HEADERS = {'Idempotency-Key': 'isolated-review-key'}


def review_client(store):
    service = CommandAdapterService(command_store=store, check_read_surface_state=lambda: None)
    app = FastAPI()
    app.include_router(create_governance_router(command_store=store, submit_action=service.submit_governance_action, extract_identity=lambda auth: REVIEW_IDENTITY))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize('different', [False, True])
def test_mounted_review_concurrent_admission(tmp_path, different):
    path = str(tmp_path / 'commands.jsonl')
    clients = [review_client(CommandStore(path)) for _ in range(2)]
    barrier = Barrier(2)
    original = CommandStore.submit_command
    def synchronized(self, *args, **kwargs):
        barrier.wait(timeout=5)
        return original(self, *args, **kwargs)
    with patch.object(CommandStore, 'submit_command', synchronized):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda i: clients[i].post('/bff/reviews', json={'review_id': f'review-{i if different else 0}'}, headers=REVIEW_HEADERS), range(2)))
    rows = CommandStore(path)._get_all_commands()
    bodies = [r.json() for r in responses]
    ids = [b.get('data', {}).get('command_id') or b.get('data', {}).get('commandId') for b in bodies]
    evidence = {'statuses': [r.status_code for r in responses], 'response_ids': ids, 'durable_ids': [r['command_id'] for r in rows], 'bodies': bodies}
    if different:
        assert sorted(r.status_code for r in responses) == [202, 409], evidence
    else:
        assert len(rows) == 1 and ids == [rows[0]['command_id']] * 2, evidence


def test_review_restart_after_receipt_write_failure(tmp_path):
    path = str(tmp_path / 'commands.jsonl')
    store = CommandStore(path)
    with patch.object(store, 'update_status', side_effect=OSError('isolated receipt write failure')):
        first = review_client(store).post('/bff/reviews', json={'review_id': 'review-0'}, headers=REVIEW_HEADERS)
    second = review_client(CommandStore(path)).post('/bff/reviews', json={'review_id': 'review-0'}, headers=REVIEW_HEADERS)
    rows = CommandStore(path)._get_all_commands()
    b = second.json()
    returned = b.get('data', {}).get('command_id') or b.get('data', {}).get('commandId')
    evidence = {'statuses': [first.status_code, second.status_code], 'returned': returned, 'durable_ids': [r['command_id'] for r in rows], 'durable_results': [r['result'] for r in rows]}
    assert second.status_code == 202 and returned == rows[0]['command_id'] and rows[0]['result'], evidence


def test_approval_decision_has_sd44_receipt(tmp_path):
    store = CommandStore(str(tmp_path / 'commands.jsonl'))
    client, _ = governance_client(store)
    r = client.post('/api/v1/approval-decisions', json=PAYLOAD, headers=REVIEW_HEADERS)
    assert r.status_code == 202
    required = {'command_id', 'aggregate_type', 'aggregate_id', 'aggregate_version', 'status', 'event_id', 'correlation_id', 'owner', 'committed_at'}
    row = store._get_all_commands()[0]
    candidates = [r.json().get('data', {}), row.get('result') or {}, (row.get('foundation') or {}).get('receipt') or {}]
    missing = [sorted(required - set(x)) for x in candidates]
    assert any(required <= set(x) for x in candidates), missing


def test_capital_pool_receipt_keeps_command_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('PANTHEON_CAPITAL_API_URL', 'http://isolated.invalid')
    store = CommandStore(str(tmp_path / 'commands.jsonl'))
    with patch('services.control_plane.bff.command_executor._post_json', side_effect=lambda url, payload: dict(payload)):
        r = capital_client(store).post('/bff/capital-pools', json={'name': 'isolated-pool'}, headers=REVIEW_HEADERS)
    assert r.status_code == 201, r.text
    row = store._get_all_commands()[0]
    assert row['result'].get('command_id') == row['command_id'], row


def deployment_client(store, identity):
    service = CommandAdapterService(command_store=store)
    deps = {name: (lambda *a, **kw: None) for name, p in inspect.signature(create_deployment_router).parameters.items() if p.default is inspect.Parameter.empty}
    deps.update(queries=SimpleNamespace(), extract_identity=lambda auth: identity, sem_command_response=service.sem_command_response)
    app = FastAPI()
    app.include_router(create_deployment_router(**deps))
    return TestClient(app)


@pytest.mark.parametrize('restart', [False, True])
def test_mounted_deployment_tenant_isolation(tmp_path, restart):
    path = str(tmp_path / 'commands.jsonl')
    headers = {'Idempotency-Key': 'review-52ce-key'}
    identity = SimpleNamespace(operator_id='shared-actor', tenant_id='tenant-a', roles=['admin'])
    client = deployment_client(CommandStore(path), identity)
    first = client.post('/bff/deployments', json={'name': 'test'}, headers=headers)
    identity.tenant_id = 'tenant-b'
    if restart:
        client = deployment_client(CommandStore(path), identity)
    second = client.post('/bff/deployments', json={'name': 'test'}, headers=headers)
    rows = CommandStore(path)._get_all_commands()
    ids = [r.json()['command_id'] for r in (first, second)]
    assert first.status_code == second.status_code == 201
    assert len(rows) == 2 and ids[0] != ids[1], {'response_ids': ids, 'rows': len(rows), 'stored_tenants': [CommandStore._tenant_id_from_command(r) for r in rows]}


@pytest.mark.parametrize('different', [False, True])
def test_mounted_deployment_concurrent_admission(tmp_path, different):
    path = str(tmp_path / 'commands.jsonl')
    headers = {'Idempotency-Key': 'review-52ce-key'}
    identity = SimpleNamespace(operator_id='shared-actor', tenant_id='tenant-a', roles=['admin'])
    clients = [deployment_client(CommandStore(path), identity) for _ in range(2)]
    barrier = Barrier(2)
    original = CommandStore.submit_command_if_no_active_target
    def synchronized(self, *args, **kwargs):
        barrier.wait(timeout=5)
        return original(self, *args, **kwargs)
    with patch.object(CommandStore, 'submit_command_if_no_active_target', synchronized):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda i: clients[i].post('/bff/deployments', json={'name': f'name-{i if different else 0}'}, headers=headers), range(2)))
    rows = CommandStore(path)._get_all_commands()
    ids = [r.json().get('command_id') for r in responses]
    evidence = {'statuses': [r.status_code for r in responses], 'response_ids': ids, 'durable_ids': [r['command_id'] for r in rows]}
    if different:
        assert sorted(r.status_code for r in responses) == [201, 409], evidence
    else:
        assert ids == [rows[0]['command_id']] * 2, evidence


@pytest.mark.parametrize('route', ['review', 'runtime'])
def test_mounted_canonical_receipt(tmp_path, monkeypatch, route):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path))
    headers = {'Idempotency-Key': 'review-52ce-key'}
    required = {'command_id', 'aggregate_type', 'aggregate_id', 'aggregate_version', 'status', 'event_id', 'correlation_id', 'owner', 'committed_at'}
    store = CommandStore(str(tmp_path / 'commands.jsonl'))
    if route == 'review':
        response = review_client(store).post('/bff/reviews', json={'review_id': 'isolated-review'}, headers=headers)
    else:
        response = _isolated_runtime_client([]).post('/bff/runtimes', json=RUNTIME_REG_PAYLOAD, headers=headers)
    assert response.status_code < 300
    row = store._get_all_commands()[0]
    data = response.json().get('data', {})
    result = row.get('result') or {}
    candidates = [data, data.get('receipt') or {}, result, result.get('data') or {}, (row.get('foundation') or {}).get('receipt') or {}]
    missing = [sorted(required - set(x)) for x in candidates]
    assert any(required <= set(x) for x in candidates), {'route': route, 'missing': missing}


class IsolatedJsonIO:
    # Only database I/O is substituted; the actual owner and router run unchanged.
    def __init__(self, path):
        self.path = path

    def rows(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def list_all(self):
        return list(self.rows().values())

    def get(self, key):
        return self.rows().get(key)

    def put(self, key, value):
        rows = self.rows()
        rows[key] = value
        self.path.write_text(json.dumps(rows))


def research_client(tmp_path, identity):
    owner = ResearchWriteOwner(
        tickets_store=IsolatedJsonIO(tmp_path / "tickets.json"),
        experiments_store=IsolatedJsonIO(tmp_path / "experiments.json"),
        notes_store=IsolatedJsonIO(tmp_path / "notes.json"),
    )
    port = DefaultResearchKnowledgeSourcePort(research_write_owner=owner)
    app = FastAPI()
    app.include_router(
        create_research_experiments_router(
            read_surface=port,
            extract_identity=lambda auth: identity,
            require_read_role=lambda i: None,
            require_operator_role=lambda i: None,
            bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
            utc_now=lambda: "2026-09-27T00:00:00Z",
        )
    )
    return TestClient(app)


@pytest.mark.parametrize("route", ["deployment", "review"])
def test_receipt_write_must_not_requeue_completed_command(tmp_path, route):
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    identity = SimpleNamespace(operator_id="actor", tenant_id="tenant", roles=["admin"])
    client = deployment_client(store, identity) if route == "deployment" else review_client(store)
    method = "submit_command_if_no_active_target" if route == "deployment" else "submit_command"
    original = getattr(store, method)
    owner_result = {"status": "executed", "owner_receipt": "isolated-owner-result"}

    def admit_and_finish(**kw):
        returned = original(**kw)
        record = returned[0] if isinstance(returned, tuple) else returned
        # An executor can finish after admission releases the store lock.
        CommandStore(store.file_path).update_status(record["command_id"], CommandStatus.EXECUTED, result=owner_result)
        return returned

    with patch.object(store, method, side_effect=admit_and_finish):
        response = client.post(
            "/bff/deployments" if route == "deployment" else "/bff/reviews",
            json={"name": "review-test"} if route == "deployment" else {"review_id": "review-test"},
            headers={"Idempotency-Key": "independent-88c0"},
        )
    assert response.status_code < 300, response.text
    row = CommandStore(store.file_path)._get_all_commands()[0]
    assert row["status"] == "executed" and row["result"] == owner_result, {
        "route": route,
        "status": row["status"],
        "result": row["result"],
    }


@pytest.mark.parametrize("change", ["restart", "actor", "tenant"])
def test_research_mounted_idempotency_scope(tmp_path, change):
    headers = {"Idempotency-Key": "independent-88c0"}
    identity = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["admin"])
    client = research_client(tmp_path, identity)
    first = client.post("/bff/experiments", json={"name": "isolated-experiment"}, headers=headers)
    if change == "restart":
        client = research_client(tmp_path, identity)
    elif change == "actor":
        identity.operator_id = "actor-b"
    else:
        identity.tenant_id = "tenant-b"
    second = client.post("/bff/experiments", json={"name": "isolated-experiment"}, headers=headers)
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    ids = [r.json()["experiment_id"] for r in (first, second)]
    rows = IsolatedJsonIO(tmp_path / "experiments.json").list_all()
    assert (ids[0] == ids[1]) == (change == "restart"), {"change": change, "ids": ids, "owner_rows": len(rows)}


def test_confirm_token_replay_keeps_tenant_target(tmp_path):
    headers = {"Idempotency-Key": "independent-88c0"}
    path = str(tmp_path / "commands.jsonl")
    identity = SimpleNamespace(operator_id="actor", tenant_id="tenant-a", roles=["admin"])
    app = FastAPI()
    app.include_router(
        create_command_adapters_router(
            command_store=CommandStore(path),
            extract_identity=lambda auth, **kw: identity,
            require_read_role=lambda i: None,
        )
    )
    client = TestClient(app)
    first = client.post("/bff/confirm-tokens", json={}, headers=headers)
    identity.tenant_id = "tenant-b"
    second = client.post("/bff/confirm-tokens", json={}, headers=headers)
    replay = client.post("/bff/confirm-tokens", json={}, headers=headers)
    assert first.status_code == second.status_code == replay.status_code == 201, [r.text for r in (first, second, replay)]
    ids = [r.json()["data"]["tokenId"] for r in (first, second, replay)]
    assert ids[1] == ids[2] and ids[0] != ids[2], {"token_ids": ids, "replay_target": replay.json()["data"]["target"]}


class AtomicIO:
    # Mirrors independent DB read snapshots and atomic UPSERTs; no product I/O.
    def __init__(self, barrier=None):
        self.rows = {}
        self.lock = Lock()
        self.barrier = barrier
        self.fail = False

    def list_all(self):
        with self.lock:
            snapshot = deepcopy(list(self.rows.values()))
        if self.barrier:
            self.barrier.wait(timeout=5)
        return snapshot

    def get(self, key):
        with self.lock:
            return deepcopy(self.rows.get(key))

    def put(self, key, value):
        if self.fail:
            raise OSError("injected experiment commit failure")
        with self.lock:
            self.rows[key] = deepcopy(value)

    def delete_if_matches(self, key, expected):
        with self.lock:
            if self.rows.get(key) == expected:
                del self.rows[key]
                return True
            return False

    def delete(self, key):
        with self.lock:
            return self.rows.pop(key, None)


class IndependentConnectionStore:
    """Distinct owner-store instances sharing SQL-like committed rows, no shared Python admission state."""

    def __init__(self, db, list_barrier=None, get_barrier=None):
        self.db = db
        self.list_barrier = list_barrier
        self.get_barrier = get_barrier
        self.probed = False

    def list_all(self):
        snapshot = self.db.list_all()
        if self.list_barrier:
            self.list_barrier.wait(timeout=10)
        return snapshot

    def get(self, key):
        snapshot = self.db.get(key)
        if self.get_barrier and not self.probed:
            self.probed = True
            self.get_barrier.wait(timeout=10)
        return snapshot

    def put(self, key, value):
        self.db.put(key, value)

    def delete_if_matches(self, key, expected):
        with self.db.lock:
            if self.db.rows.get(key) == expected:
                del self.db.rows[key]
                return True
            return False

    def delete(self, key):
        with self.db.lock:
            return self.db.rows.pop(key, None)


class BlockFailTickets(AtomicIO):
    def __init__(self):
        super().__init__()
        self.entered = Event()
        self.release = Event()
        self.armed = False

    def put(self, key, value):
        if self.armed:
            self.entered.set()
            assert self.release.wait(timeout=10)
            raise OSError("injected ticket storage failure")
        return super().put(key, value)


def _atomic_research_client(tickets, experiments, tenant):
    owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())
    app = FastAPI()
    identity = SimpleNamespace(operator_id="actor", tenant_id=tenant, roles=["admin"])
    app.include_router(
        create_research_experiments_router(
            read_surface=DefaultResearchKnowledgeSourcePort(research_write_owner=owner),
            extract_identity=lambda auth: identity,
            require_read_role=lambda i: None,
            require_operator_role=lambda i: None,
            bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
            utc_now=lambda: "2026-09-28T00:00:00Z",
        )
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("separate_tenants", [False, True])
def test_concurrent_experiment_admission(separate_tenants):
    tickets, experiments = AtomicIO(), AtomicIO(Barrier(2))
    clients = [_atomic_research_client(tickets, experiments, "tenant-" + str(i if separate_tenants else 0)) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda i: clients[i].post(
                    "/bff/experiments",
                    json={"name": "experiment-" + str(i)},
                    headers={"Idempotency-Key": "same-key"},
                ),
                range(2),
            )
        )
    statuses = [r.status_code for r in responses]
    evidence = {"statuses": statuses, "ids": [r.json().get("experiment_id") for r in responses], "durable_rows": experiments.rows}
    if separate_tenants:
        assert statuses == [201, 201] and len(experiments.rows) == 2, evidence
    else:
        assert sorted(statuses) == [201, 409], evidence


def test_experiment_failure_rolls_back_ticket_link():
    tickets, experiments = AtomicIO(), AtomicIO()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    experiments.fail = True
    response = _atomic_research_client(tickets, experiments, "tenant").post(
        "/bff/experiments",
        json={"name": "experiment", "ticket_id": "ticket-1"},
        headers={"Idempotency-Key": "key"},
    )
    evidence = {"status": response.status_code, "ticket": tickets.rows, "experiments": experiments.rows}
    assert response.status_code >= 500
    assert tickets.get("ticket-1")["linked_experiments"] == [], evidence


@pytest.mark.parametrize("different_tenants", [False, True])
def test_independent_owners_share_database(different_tenants):
    tickets, db = AtomicIO(), AtomicIO()
    listed, probed = Barrier(2), Barrier(2)
    clients = [
        _atomic_research_client(tickets, IndependentConnectionStore(db, listed, probed), "tenant-" + str(i if different_tenants else 0))
        for i in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda i: clients[i].post(
                    "/bff/experiments",
                    json={"name": "experiment-" + str(i)},
                    headers={"Idempotency-Key": "key"},
                ),
                range(2),
            )
        )
    statuses = [r.status_code for r in responses]
    evidence = {"statuses": statuses, "ids": [r.json().get("experiment_id") for r in responses], "rows": db.rows}
    if different_tenants:
        assert statuses == [201, 201] and len(db.rows) == 2, evidence
    else:
        assert sorted(statuses) == [201, 409], evidence


def test_retry_does_not_accept_before_ticket_transaction_commits():
    tickets, db = BlockFailTickets(), AtomicIO()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    tickets.armed = True
    client = _atomic_research_client(tickets, IndependentConnectionStore(db), "tenant")
    request = lambda: client.post(
        "/bff/experiments",
        json={"name": "experiment", "ticket_id": "ticket-1"},
        headers={"Idempotency-Key": "key"},
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request)
        assert tickets.entered.wait(timeout=10)
        replay = request()
        tickets.release.set()
        failed = first.result(timeout=10)
    evidence = {
        "first_status": failed.status_code,
        "replay_status": replay.status_code,
        "durable_experiments": db.rows,
        "ticket": tickets.rows,
    }
    assert not (replay.status_code == 201 and not db.rows), evidence
    assert failed.status_code >= 500, evidence
    assert replay.status_code == 409, evidence
    assert len(db.rows) == 0, evidence


def test_independent_owner_restart_replay_and_query_visibility():
    tickets, db = AtomicIO(), AtomicIO()
    client1 = _atomic_research_client(tickets, IndependentConnectionStore(db), "tenant-1")
    r1 = client1.post("/bff/experiments", json={"name": "experiment-alpha"}, headers={"Idempotency-Key": "key-alpha"})
    assert r1.status_code == 201
    exp_id = r1.json()["experiment_id"]

    # New client simulating separate process / restart with independent store wrapping same db
    client2 = _atomic_research_client(tickets, IndependentConnectionStore(db), "tenant-1")
    # Replay with same body returns 201 with same experiment_id
    r2 = client2.post("/bff/experiments", json={"name": "experiment-alpha"}, headers={"Idempotency-Key": "key-alpha"})
    assert r2.status_code == 201
    assert r2.json()["experiment_id"] == exp_id
    assert len(db.rows) == 1

    # Replay with conflicting body returns 409
    r3 = client2.post("/bff/experiments", json={"name": "experiment-beta"}, headers={"Idempotency-Key": "key-alpha"})
    assert r3.status_code == 409

    # Querying experiment by ID and list
    r_get = client2.get(f"/bff/experiments/{exp_id}")
    assert r_get.status_code == 200
    assert r_get.json()["data"]["experiment_id"] == exp_id

    r_list = client2.get("/bff/experiments")
    assert r_list.status_code == 200
    items = r_list.json().get("items") or r_list.json().get("data") or []
    assert any(e["experiment_id"] == exp_id for e in items)


class CASStore(AtomicIO):
    def compare_and_set(self, key, expected, value, *, conn=None):
        with self.lock:
            current = self.rows.get(key)
            if current != expected:
                return False, deepcopy(current)
            self.rows[key] = deepcopy(value)
            return True, deepcopy(value)


class FailFinalCommit(CASStore):
    armed = True

    def compare_and_set(self, key, expected, value, *, conn=None):
        if self.armed and value.get("is_committed"):
            raise OSError("independent injected final commit failure")
        return super().compare_and_set(key, expected, value, conn=conn)

    def put(self, key, value):
        if self.armed and value.get("is_committed"):
            raise OSError("independent injected final commit failure")
        super().put(key, value)


class SnapshotTickets(CASStore):
    barrier = None

    def get(self, key):
        snapshot = super().get(key)
        if self.barrier:
            self.barrier.wait(timeout=10)
        return snapshot


def test_final_commit_failure_is_recoverable_after_restart():
    tickets, experiments = CASStore(), FailFinalCommit()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    client = _atomic_research_client(tickets, experiments, "tenant")
    body = {"name": "experiment", "ticket_id": "ticket-1"}
    headers = {"Idempotency-Key": "key"}
    first = client.post("/bff/experiments", json=body, headers=headers)
    experiments.armed = False
    restarted = _atomic_research_client(tickets, experiments, "tenant")
    retry = restarted.post("/bff/experiments", json=body, headers=headers)
    evidence = {
        "first": first.status_code,
        "retry": retry.status_code,
        "retry_body": retry.text,
        "links": tickets.get("ticket-1")["linked_experiments"],
        "rows": [(k, v["is_committed"]) for k, v in experiments.rows.items()],
    }
    assert first.status_code >= 500
    assert retry.status_code == 201, evidence


def test_concurrent_experiments_preserve_both_ticket_links():
    tickets, experiments = SnapshotTickets(), CASStore()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    tickets.barrier = Barrier(2)
    clients = [_atomic_research_client(tickets, experiments, "tenant") for _ in range(2)]

    def submit(i):
        return clients[i].post(
            "/bff/experiments",
            json={"name": f"experiment-{i}", "ticket_id": "ticket-1"},
            headers={"Idempotency-Key": f"key-{i}"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    tickets.barrier = None
    ids = {r.json().get("experiment_id") for r in responses}
    links = tickets.get("ticket-1")["linked_experiments"]
    evidence = {
        "statuses": [r.status_code for r in responses],
        "experiment_ids": sorted(ids),
        "ticket_links": links,
    }
    assert [r.status_code for r in responses] == [201, 201], evidence
    assert len(ids) == 2 and ids == set(links), evidence


def test_retry_experiment_final_commit_failure_and_recovery():
    tickets, experiments = CASStore(), FailFinalCommit()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    client = _atomic_research_client(tickets, experiments, "tenant")
    experiments.armed = False
    init_res = client.post(
        "/bff/experiments",
        json={"name": "exp-failed", "ticket_id": "ticket-1"},
        headers={"Idempotency-Key": "init-key"},
    )
    assert init_res.status_code == 201
    exp_id = init_res.json()["experiment_id"]
    failed_exp = experiments.rows[exp_id]
    failed_exp["status"] = "failed"
    experiments.put(exp_id, failed_exp)

    experiments.armed = True
    owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())
    with pytest.raises(OSError):
        owner.retry_research_experiment(exp_id, actor_id="actor", idempotency_key="retry-key")

    experiments.armed = False
    restarted_owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())
    retried = restarted_owner.retry_research_experiment(exp_id, actor_id="actor", idempotency_key="retry-key")
    assert retried is not None
    new_id = retried["experiment_id"]
    assert experiments.rows[new_id]["is_committed"] is True
    assert retried["attempt_number"] == 2
    assert new_id in tickets.get("ticket-1")["linked_experiments"]


class LostAdmissionAck(CASStore):
    armed = True

    def compare_and_set(self, key, expected, value, *, conn=None):
        result = super().compare_and_set(key, expected, value, conn=conn)
        if self.armed and result[0]:
            self.armed = False
            raise OSError("connection lost after admission committed, before ticket update")
        return result


class PausedFirstFinalCommit(CASStore):
    def __init__(self):
        super().__init__()
        self.entered, self.release = Event(), Event()
        self.armed = True

    def compare_and_set(self, key, expected, value, *, conn=None):
        if self.armed and value.get("is_committed"):
            self.armed = False
            self.entered.set()
            assert self.release.wait(timeout=15)
        return super().compare_and_set(key, expected, value, conn=conn)

    def put(self, key, value):
        if self.armed and value.get("is_committed"):
            self.armed = False
            self.entered.set()
            assert self.release.wait(timeout=15)
        return super().put(key, value)


def _full_research_client(tickets, experiments, tenant="tenant", actor="actor"):
    owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())
    app = FastAPI()
    identity = SimpleNamespace(operator_id=actor, tenant_id=tenant, roles=["admin"])
    app.include_router(
        create_research_router(
            read_surface=DefaultResearchKnowledgeSourcePort(research_write_owner=owner),
            extract_identity=lambda auth: identity,
            require_read_role=lambda i: None,
            require_operator_role=lambda i: None,
            bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
            utc_now=lambda: "2026-09-28T00:00:00Z",
        )
    )
    return TestClient(app, raise_server_exceptions=False)


def test_restart_recovers_admission_before_ticket_link():
    tickets, experiments = CASStore(), LostAdmissionAck()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    body = {"name": "experiment", "ticket_id": "ticket-1"}
    headers = {"Idempotency-Key": "independent-ack-test"}
    first = _full_research_client(tickets, experiments).post("/bff/experiments", json=body, headers=headers)
    restarted = _full_research_client(tickets, experiments)
    retries = [restarted.post("/bff/experiments", json=body, headers=headers) for _ in range(2)]
    evidence = {
        "first": first.status_code,
        "retries": [r.status_code for r in retries],
        "committed": [r["is_committed"] for r in experiments.rows.values()],
        "links": tickets.get("ticket-1")["linked_experiments"],
    }
    assert first.status_code >= 500
    assert retries[-1].status_code == 201, evidence
    assert all(r["is_committed"] for r in experiments.rows.values()), evidence
    assert len(tickets.get("ticket-1")["linked_experiments"]) == 1, evidence


def test_delayed_final_commit_does_not_undo_accepted_cancel():
    tickets, experiments = CASStore(), PausedFirstFinalCommit()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    body = {"name": "experiment", "ticket_id": "ticket-1"}
    headers = {"Idempotency-Key": "delayed-put-test"}
    first_client, second_client = _full_research_client(tickets, experiments), _full_research_client(tickets, experiments)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(first_client.post, "/bff/experiments", json=body, headers=headers)
        assert experiments.entered.wait(timeout=10)
        try:
            replay = second_client.post("/bff/experiments", json=body, headers=headers)
            assert replay.status_code == 201, replay.text
            eid = replay.json()["experiment_id"]
            canceled = second_client.post(f"/api/v1/experiments/{eid}/cancel", json={"reason": "isolated cancellation"})
            assert canceled.status_code == 200, canceled.text
            assert experiments.get(eid)["status"] == "canceled"
        finally:
            experiments.release.set()
        response = first.result(timeout=10)
    final = experiments.get(eid)
    evidence = {
        "first": response.status_code,
        "replay": replay.status_code,
        "cancel": canceled.status_code,
        "cancel_result": canceled.json()["status"],
        "final_status": final["status"],
        "final_cancellation_fence": final.get("cancellation_fence"),
    }
    assert response.status_code == 201, evidence
    assert final["status"] == "canceled" and final.get("cancellation_fence"), evidence


def test_mounted_launch_honors_scoped_idempotency_and_isolation():
    tickets, experiments = CASStore(), CASStore()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    payload = {
        "ticket_id": "ticket-1",
        "experiment_name": "isolated",
        "strategy_selector": {},
        "parameter_set": {},
        "run_config": {
            "dataset_ref": "isolated",
            "time_range": {"start_at": "2026-01-01", "end_at": "2026-01-02"},
            "execution_mode": "paper",
            "requested_by": "actor",
        },
        "launch_context": {},
    }
    headers = {"Idempotency-Key": "independent-launch-key"}
    client1 = _full_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")

    # 1. First launch succeeds
    first = client1.post("/api/v1/experiments/launch", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    eid = first.json()["experiment_id"]

    # Durable row checks
    exp_row = experiments.rows[eid]
    assert exp_row.get("idempotency_key") == "independent-launch-key"
    assert exp_row.get("tenant_id") == "tenant-a"
    assert exp_row.get("actor_id") == "actor-a"

    # 2. Replay with identical payload and same key (simulating process restart)
    client_restart = _full_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")
    second = client_restart.post("/api/v1/experiments/launch", json=payload, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["experiment_id"] == eid

    # 3. Conflicting payload with same key returns 409
    conflicting_payload = dict(payload, experiment_name="conflict-name")
    conflict = client1.post("/api/v1/experiments/launch", json=conflicting_payload, headers=headers)
    assert conflict.status_code == 409, conflict.text

    # 4. Same key with different tenant provides isolation
    client2 = _full_research_client(tickets, experiments, tenant="tenant-b", actor="actor-b")
    isolated = client2.post("/api/v1/experiments/launch", json=payload, headers=headers)
    assert isolated.status_code == 200, isolated.text
    assert isolated.json()["experiment_id"] != eid
    assert len(experiments.rows) == 2


def test_launch_denies_read_only_actor():
    tickets, experiments = CASStore(), CASStore()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())
    identity = SimpleNamespace(operator_id="reader", tenant_id="tenant", roles=["viewer"])

    def deny_operator(i):
        raise HTTPException(403, detail="operator role required")

    app = FastAPI()
    app.include_router(
        create_research_router(
            read_surface=DefaultResearchKnowledgeSourcePort(research_write_owner=owner),
            extract_identity=lambda a: identity,
            require_read_role=lambda i: None,
            require_operator_role=deny_operator,
            bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
            utc_now=lambda: "2026-09-28T00:00:00Z",
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    control = client.post("/bff/experiments", json={"name": "denied"}, headers={"Idempotency-Key": "control"})
    assert control.status_code == 403
    payload = {
        "ticket_id": "ticket-1",
        "experiment_name": "isolated",
        "strategy_selector": {},
        "parameter_set": {},
        "run_config": {
            "dataset_ref": "isolated",
            "time_range": {"start_at": "2026-01-01", "end_at": "2026-01-02"},
            "execution_mode": "paper",
            "requested_by": "reader",
        },
        "launch_context": {},
    }
    result = client.post("/api/v1/experiments/launch", json=payload, headers={"Idempotency-Key": "denied-launch"})
    evidence = {"canonical_create": control.status_code, "launch": result.status_code, "durable_rows": len(experiments.rows)}
    assert result.status_code == 403 and not experiments.rows, evidence


class ProductionCASStore(CASStore):
    table = '"research"."review_experiments"'
    table_name = "research.review_experiments"
    read_only = False
    owner_service = "research-svc"

    def __init__(self):
        super().__init__()
        self.entered, self.release = Event(), Event()
        self.armed = True
        self._cursor = []

    @contextmanager
    def _connect(self):
        yield self

    def execute(self, sql, params=None):
        sql_clean = " ".join(sql.split())
        if "UPDATE" in sql_clean:
            encoded_cand, record_id, encoded_expected = params
            cand = json.loads(encoded_cand)
            expected = json.loads(encoded_expected)
            if self.armed and cand.get("is_committed"):
                self.armed = False
                self.entered.set()
                assert self.release.wait(15)
            with self.lock:
                curr = self.rows.get(record_id)
                if curr == expected:
                    self.rows[record_id] = deepcopy(cand)
                    self._cursor = [(cand,)]
                else:
                    self._cursor = []
        elif "SELECT payload FROM" in sql_clean:
            record_id = params[0]
            with self.lock:
                curr = self.rows.get(record_id)
                self._cursor = [(curr,)] if curr is not None else []
        elif "INSERT INTO" in sql_clean:
            record_id, encoded = params
            cand = json.loads(encoded)
            with self.lock:
                if record_id not in self.rows:
                    self.rows[record_id] = deepcopy(cand)
                    self._cursor = [(cand,)]
                else:
                    self._cursor = []
        return self

    def fetchone(self):
        return self._cursor[0] if getattr(self, "_cursor", None) else None

    def fetchall(self):
        return getattr(self, "_cursor", [])

    compare_and_set = PostgresJsonOwnerStore.compare_and_set
    _fetch_one = staticmethod(PostgresJsonOwnerStore._fetch_one)
    _decode_payload = staticmethod(PostgresJsonOwnerStore._decode_payload)
    _use_conn = PostgresJsonOwnerStore._use_conn


def test_production_serialization_preserves_concurrent_cancel():
    tickets, experiments = CASStore(), ProductionCASStore()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    client1 = _full_research_client(tickets, experiments)
    client2 = _full_research_client(tickets, experiments)
    body = {"name": "isolated", "ticket_id": "ticket-1"}
    headers = {"Idempotency-Key": "prod-serialization-test"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client1.post, "/bff/experiments", json=body, headers=headers)
        assert experiments.entered.wait(10)
        try:
            replay = client2.post("/bff/experiments", json=body, headers=headers)
            assert replay.status_code == 201, replay.text
            eid = replay.json()["experiment_id"]
            canceled = client2.post(f"/api/v1/experiments/{eid}/cancel", json={"reason": "isolated cancellation"})
            assert canceled.status_code == 200, canceled.text
            assert experiments.get(eid)["status"] == "canceled"
        finally:
            experiments.release.set()
        first = pending.result(10)
    row = experiments.get(eid)
    evidence = {
        "first": first.status_code,
        "replay": replay.status_code,
        "cancel": canceled.status_code,
        "final_status": row["status"],
        "final_fence": row.get("cancellation_fence"),
    }
    assert row["status"] == "canceled" and row.get("cancellation_fence"), evidence


class ContendedConnection:
    # Same get/put/CAS API as production; no exposed rows or process lock.
    def __init__(self):
        self.backend = CASStore()
        self.conflicts = 0
        self.before_put = None

    def _serialize(self, value):
        if value is None:
            return None
        return json.loads(json.dumps(value))

    def get(self, key):
        return self._serialize(self.backend.get(key))

    def list_all(self):
        return [self._serialize(r) for r in self.backend.list_all()]

    def compare_and_set(self, key, expected, candidate, *, conn=None):
        if expected is not None and self.before_put:
            cb, self.before_put = self.before_put, None
            cb(key)
        if expected is not None and self.conflicts < 6:
            self.conflicts += 1
            current = self.backend.get(key)
            current["updated_at"] = f"concurrent-update-{self.conflicts}"
            self.backend.put(key, self._serialize(current))
            return False, self._serialize(current)
        return self.backend.compare_and_set(
            key,
            self._serialize(expected),
            self._serialize(candidate),
        )

    def put(self, key, value):
        callback, self.before_put = self.before_put, None
        if callback:
            callback(key)
        self.backend.put(key, self._serialize(value))


def test_cas_exhaustion_preserves_concurrent_cancellation():
    tickets, store = CASStore(), ContendedConnection()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    first, second = _full_research_client(tickets, store), _full_research_client(tickets, store)
    body, headers = {"name": "review", "ticket_id": "ticket-1"}, {"Idempotency-Key": "review-cas-exhaustion"}
    result = {}

    def concurrent_completion_and_cancel(eid):
        replay = second.post("/bff/experiments", json=body, headers=headers)
        canceled = second.post(f"/api/v1/experiments/{eid}/cancel", json={"reason": "review"})
        result.update(replay=replay.status_code, cancel=canceled.status_code, before_stale_put=store.get(eid)["status"])

    store.before_put = concurrent_completion_and_cancel
    response = first.post("/bff/experiments", json=body, headers=headers)
    eid = response.json()["experiment_id"]
    row = store.get(eid)
    result.update(first=response.status_code, conflicts=store.conflicts, final_status=row["status"], final_fence=row.get("cancellation_fence"))
    assert row["status"] == "canceled" and row.get("cancellation_fence"), result


def test_cas_exhaustion_fails_closed():
    # If CAS continuously conflicts beyond retry limit, fail closed rather than falling back to unconditional put.
    class EndlessConflictStore(CASStore):
        def compare_and_set(self, key, expected, value, *, conn=None):
            if expected is not None:
                current = self.rows.get(key, {})
                current["updated_at"] = "conflict"
                return False, deepcopy(current)
            return super().compare_and_set(key, expected, value, conn=conn)

    tickets, store = CASStore(), EndlessConflictStore()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "linked_experiments": []})
    client = _full_research_client(tickets, store)
    body, headers = {"name": "fail_closed", "ticket_id": "ticket-1"}, {"Idempotency-Key": "fail-closed-key"}
    response = client.post("/bff/experiments", json=body, headers=headers)
    assert response.status_code >= 500, response.text


def test_midnight_scoped_idempotency_independent_of_date_prefix():
    tickets, store = CASStore(), CASStore()
    store.barrier = Barrier(2)

    def client_at(timestamp):
        owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=store, notes_store=AtomicIO())
        app = FastAPI()
        app.include_router(
            create_research_router(
                read_surface=DefaultResearchKnowledgeSourcePort(research_write_owner=owner),
                extract_identity=lambda a: SimpleNamespace(operator_id="actor", tenant_id="tenant", roles=["admin"]),
                require_read_role=lambda i: None,
                require_operator_role=lambda i: None,
                bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
                utc_now=lambda: timestamp,
            )
        )
        return TestClient(app, raise_server_exceptions=False)

    clients = [client_at(t) for t in ("2026-09-27T23:59:59Z", "2026-09-28T00:00:00Z")]

    def submit(c):
        return c.post("/bff/experiments", json={"name": "review"}, headers={"Idempotency-Key": "same-key"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, clients))
    result = {"status": [r.status_code for r in responses], "ids": [r.json().get("experiment_id") for r in responses], "rows": len(store.rows)}
    assert [r.status_code for r in responses] == [201, 201], result
    assert len(set(result["ids"])) == 1, result
    assert len(store.rows) == 1, result


def _research_ticket_client(tickets, role="admin", utc_now=None, identity=None, experiments=None):
    owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments if experiments is not None else CASStore(), notes_store=AtomicIO())
    if identity is None:
        identity = SimpleNamespace(operator_id="actor", tenant_id="tenant", roles=[role])

    def require_operator(i):
        roles = set(getattr(i, "roles", []) or [])
        if not roles.intersection({"admin", "operator", "approver", "reviewer"}):
            raise HTTPException(403, detail="operator required")

    app = FastAPI()
    app.include_router(
        create_research_router(
            read_surface=DefaultResearchKnowledgeSourcePort(research_write_owner=owner),
            extract_identity=lambda auth: identity,
            require_read_role=lambda i: None,
            require_operator_role=require_operator,
            bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
            utc_now=utc_now or (lambda: "2026-09-28T00:00:00Z"),
        )
    )
    return TestClient(app, raise_server_exceptions=False)


TICKET_BODY = {"title": "Independent review", "description": "isolated test", "priority": "normal", "owner": "actor"}


def test_ticket_create_rejects_viewer():
    tickets = CASStore()
    c = _research_ticket_client(tickets, "viewer")
    control = c.post("/bff/experiments", json={"name": "must be denied"})
    r = c.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-key"})
    evidence = {"control": control.status_code, "ticket": r.status_code, "rows": len(tickets.rows)}
    assert control.status_code == 403
    assert r.status_code == 403 and not tickets.rows, evidence


def test_ticket_restart_replay_and_receipt():
    tickets = CASStore()
    client = _research_ticket_client(tickets)
    r1 = client.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-key"})
    r2 = client.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-key"})
    fresh = _research_ticket_client(tickets)
    detail = fresh.get("/api/v1/research/tickets/" + r1.json()["ticket_id"])
    evidence = {
        "statuses": [r1.status_code, r2.status_code],
        "ids": [r1.json()["ticket_id"], r2.json()["ticket_id"]],
        "owner_rows": len(tickets.rows),
        "fresh_detail_status": detail.status_code,
        "body": r1.json(),
    }
    assert r1.json()["ticket_id"] == r2.json()["ticket_id"] and detail.status_code == 200 and tickets.rows, evidence
    receipt = r1.json().get("receipt")
    assert receipt and receipt.get("command_id"), evidence
    assert receipt.get("aggregate_type") == "research_ticket"


def test_ticket_patch_rejects_viewer():
    tickets = CASStore()
    admin_client = _research_ticket_client(tickets, "admin")
    created = admin_client.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-key"})
    assert created.status_code == 200
    ticket_id = created.json()["ticket_id"]

    viewer_client = _research_ticket_client(tickets, "viewer")
    patched = viewer_client.patch(f"/api/v1/research/tickets/{ticket_id}", json={"status": "closed"}, headers={"Idempotency-Key": "patch-key"})
    assert patched.status_code == 403


def test_ticket_patch_durability_and_replay():
    tickets = CASStore()
    client = _research_ticket_client(tickets, "admin")
    created = client.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-key"})
    assert created.status_code == 200
    ticket_id = created.json()["ticket_id"]

    p1 = client.patch(f"/api/v1/research/tickets/{ticket_id}", json={"status": "closed"}, headers={"Idempotency-Key": "patch-key"})
    assert p1.status_code == 200
    assert p1.json()["status"] == "closed"

    # Fresh client observes persisted closed status
    fresh = _research_ticket_client(tickets, "admin")
    readback = fresh.get(f"/api/v1/research/tickets/{ticket_id}")
    assert readback.status_code == 200
    assert readback.json()["status"] == "closed"


def test_approval_caller_id_cannot_alias_other_tenant_command(tmp_path):
    path = str(tmp_path / "commands.jsonl")
    a, _ = governance_client(CommandStore(path), SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["admin"]))
    b, _ = governance_client(CommandStore(path), SimpleNamespace(operator_id="actor-b", tenant_id="tenant-b", roles=["admin"]))
    r1 = a.post("/api/v1/approval-decisions", json={**PAYLOAD, "decision_id": "shared-id"}, headers={"Idempotency-Key": "key-a"})
    r2 = b.post("/api/v1/approval-decisions", json={**PAYLOAD, "decision_id": "shared-id", "decision": "reject"}, headers={"Idempotency-Key": "key-b"})
    persisted = CommandStore(path).get_command(r2.json()["data"]["command_id"])
    evidence = {
        "statuses": [r1.status_code, r2.status_code],
        "ids": [r1.json()["data"]["command_id"], r2.json()["data"]["command_id"]],
        "second_receipt_actor": r2.json()["data"]["approver_id"],
        "readback_actor": persisted["audit"]["operator_id"],
    }
    assert r2.status_code == 409 or persisted["audit"]["operator_id"] == "actor-b", evidence


def test_approval_conflicting_command_id_rejected(tmp_path):
    path = str(tmp_path / "commands.jsonl")
    store = CommandStore(path)
    a, _ = governance_client(store, SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["admin"]))
    b, _ = governance_client(store, SimpleNamespace(operator_id="actor-b", tenant_id="tenant-b", roles=["admin"]))
    r1 = a.post("/api/v1/approval-decisions", json={**PAYLOAD, "command_id": "explicit-cmd-id"}, headers={"Idempotency-Key": "key-a"})
    assert r1.status_code == 202
    r2 = b.post("/api/v1/approval-decisions", json={**PAYLOAD, "command_id": "explicit-cmd-id", "decision": "reject"}, headers={"Idempotency-Key": "key-b"})
    assert r2.status_code == 409
    # Confirm original command was unchanged
    persisted = store.get_command("explicit-cmd-id")
    assert persisted["audit"]["operator_id"] == "actor-a"


def test_research_ticket_midnight_admission_race():
    from threading import Barrier, local

    class SynchronizedTickets(AtomicIO):
        def __init__(self):
            super().__init__()
            self.first_snapshot = Barrier(2)
            self.thread_local = local()

        def list_all(self):
            snapshot = super().list_all()
            if not getattr(self.thread_local, "seen", False):
                self.thread_local.seen = True
                self.first_snapshot.wait(timeout=10)
            return snapshot

    tickets = SynchronizedTickets()
    owners = [ResearchWriteOwner(tickets_store=tickets, experiments_store=AtomicIO(), notes_store=AtomicIO()) for _ in range(2)]
    timestamps = ["2026-09-27T23:59:59Z", "2026-09-28T00:00:00Z"]

    def submit(index):
        return owners[index].create_research_ticket(
            title="same ticket", description="same body", priority="normal", owner="actor",
            actor_id="actor", tenant_id="tenant", idempotency_key="same-key", request_hash="same-payload-hash",
            created_at=timestamps[index],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    ids = [row["ticket_id"] for row in results]
    assert len(set(ids)) == 1 and len(tickets.rows) == 1


def test_create_replay_survives_patch():
    tickets = CASStore()
    client = _research_ticket_client(tickets)
    first = client.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "create-key"})
    assert first.status_code == 200
    tid = first.json()["ticket_id"]
    changed = client.patch("/api/v1/research/tickets/" + tid, json={"title": "new title"}, headers={"Idempotency-Key": "patch-key"})
    assert changed.status_code == 200
    retry = _research_ticket_client(tickets).post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "create-key"})
    assert retry.status_code == 200
    assert retry.json()["ticket_id"] == tid, {"first": tid, "retry": retry.json()["ticket_id"], "rows": len(tickets.rows)}


def test_patch_conflicting_payload_is_rejected():
    tickets = CASStore()
    client = _research_ticket_client(tickets)
    first = client.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "create-key"})
    assert first.status_code == 200
    path = "/api/v1/research/tickets/" + first.json()["ticket_id"]
    patched = client.patch(path, json={"title": "one"}, headers={"Idempotency-Key": "patch-key"})
    assert patched.status_code == 200
    assert patched.json().get("aggregate_version") == 2
    assert patched.json().get("receipt", {}).get("command") == "PatchResearchTicket"
    retry = _research_ticket_client(tickets).patch(path, json={"title": "two"}, headers={"Idempotency-Key": "patch-key"})
    assert retry.status_code == 409, {"status": retry.status_code, "title": retry.json().get("title"), "receipt": retry.json().get("receipt")}


def test_ticket_writer_absent_returns_503_without_local_mutation():
    port = DefaultResearchKnowledgeSourcePort(research_tickets_store={"seed": {"ticket_id": "seed", "title": "seed", "status": "open"}})
    port._get_research_write_owner = lambda: None
    app = FastAPI()
    app.include_router(create_research_router(
        read_surface=port,
        extract_identity=lambda auth: SimpleNamespace(operator_id="actor", tenant_id="tenant", roles=["admin"]),
        require_read_role=lambda identity: None,
        require_operator_role=lambda identity: None,
        bff_error=lambda status, code, message, *args, **kwargs: HTTPException(status, detail=message),
        utc_now=lambda: "2026-09-28T00:00:00Z",
    ))
    client = TestClient(app, raise_server_exceptions=False)
    create_resp = client.post(
        "/api/v1/research/tickets",
        json={"title": "no owner", "description": "process local", "priority": "normal", "owner": "actor"},
        headers={"Idempotency-Key": "local-write-key"},
    )
    assert create_resp.status_code == 503
    assert len(port._tickets) == 1

    patch_resp = client.patch(
        "/api/v1/research/tickets/seed",
        json={"title": "patch without owner"},
        headers={"Idempotency-Key": "local-patch-key"},
    )
    assert patch_resp.status_code == 503
    assert port._tickets["seed"]["title"] == "seed"


def test_restart_replay_after_ticket_close():
    tickets = CASStore()
    client = _research_ticket_client(tickets)
    created = client.post('/api/v1/research/tickets', json=TICKET_BODY, headers={'Idempotency-Key': 'create'})
    assert created.status_code == 200
    path = '/api/v1/research/tickets/' + created.json()['ticket_id']
    body = {'title': 'final title', 'status': 'closed'}
    first = client.patch(path, json=body, headers={'Idempotency-Key': 'close'})
    retry = _research_ticket_client(tickets).patch(path, json=body, headers={'Idempotency-Key': 'close'})
    assert first.status_code == 200
    assert retry.status_code == 200, {'first': first.status_code, 'retry': retry.status_code, 'body': retry.json()}
    assert retry.json()['receipt'] == first.json()['receipt']


def test_cas_conflict_rechecks_lifecycle():
    class ConcurrentClose(CASStore):
        armed = False
        def compare_and_set(self, key, expected, value, *, conn=None):
            if self.armed:
                self.armed = False
                response = _research_ticket_client(self).patch('/api/v1/research/tickets/' + key, json={'status': 'closed'}, headers={'Idempotency-Key': 'concurrent-close'})
                assert response.status_code == 200, response.text
            return super().compare_and_set(key, expected, value, conn=conn)
    tickets = ConcurrentClose()
    client = _research_ticket_client(tickets)
    created = client.post('/api/v1/research/tickets', json=TICKET_BODY, headers={'Idempotency-Key': 'create'})
    assert created.status_code == 200
    tid = created.json()['ticket_id']
    tickets.armed = True
    result = client.patch('/api/v1/research/tickets/' + tid, json={'title': 'stale edit'}, headers={'Idempotency-Key': 'edit'})
    canonical = tickets.get(tid)
    assert result.status_code == 409 and canonical['title'] == TICKET_BODY['title'], {'http': result.status_code, 'title': canonical['title'], 'state': canonical['status'], 'version': canonical['aggregate_version']}


def test_patch_replay_canonical_identity_after_later_patch():
    tickets = CASStore()
    client = _research_ticket_client(tickets)
    created = client.post('/api/v1/research/tickets', json=TICKET_BODY, headers={'Idempotency-Key': 'create'})
    path = '/api/v1/research/tickets/' + created.json()['ticket_id']
    original = client.patch(path, json={'title': 'first'}, headers={'Idempotency-Key': 'first'}).json()
    latest = client.patch(path, json={'title': 'second'}, headers={'Idempotency-Key': 'second'}).json()
    replay = _research_ticket_client(tickets).patch(path, json={'title': 'first'}, headers={'Idempotency-Key': 'first'})
    assert replay.status_code == 200
    data = replay.json()
    assert data['event_id'] == data['receipt']['event_id'], {'original': original, 'latest': latest, 'replay': data}
    assert data == original


def test_create_replay_canonical_identity_after_patch():
    tickets = CASStore()
    client = _research_ticket_client(tickets)
    original = client.post('/api/v1/research/tickets', json=TICKET_BODY, headers={'Idempotency-Key': 'create'}).json()
    path = '/api/v1/research/tickets/' + original['ticket_id']
    changed = client.patch(path, json={'title': 'changed'}, headers={'Idempotency-Key': 'patch'})
    assert changed.status_code == 200
    replay = _research_ticket_client(tickets).post('/api/v1/research/tickets', json=TICKET_BODY, headers={'Idempotency-Key': 'create'}).json()
    assert replay['event_id'] == replay['receipt']['event_id'], {'original': original, 'replay': replay}
    assert replay == original


def test_ticket_patch_rejects_foreign_tenant():
    tickets = CASStore()
    identity_a = SimpleNamespace(operator_id='actor-a', tenant_id='tenant-a', roles=['operator'])
    client_a = _research_ticket_client(tickets, identity=identity_a)
    original = client_a.post('/api/v1/research/tickets', json=TICKET_BODY, headers={'Idempotency-Key': 'create'})
    assert original.status_code == 200
    tid = original.json()['ticket_id']
    identity_b = SimpleNamespace(operator_id='actor-b', tenant_id='tenant-b', roles=['operator'])
    client_b = _research_ticket_client(tickets, identity=identity_b)
    response = client_b.patch('/api/v1/research/tickets/' + tid, json={'title': 'foreign tenant mutation'}, headers={'Idempotency-Key': 'foreign'})
    assert response.status_code in (403, 404), {'status': response.status_code, 'body': response.json(), 'persisted': tickets.get(tid)}
    assert tickets.get(tid)['title'] == TICKET_BODY['title']


def test_restart_replay_preserves_canonical_identity_after_subsequent_mutation():
    tickets = CASStore()
    client = _research_ticket_client(tickets)
    created = client.post('/api/v1/research/tickets', json=TICKET_BODY, headers={'Idempotency-Key': 'create'}).json()
    tid = created['ticket_id']
    path = f'/api/v1/research/tickets/{tid}'

    patch1 = client.patch(path, json={'title': 'patch-1'}, headers={'Idempotency-Key': 'patch-1'}).json()
    patch2 = client.patch(path, json={'title': 'patch-2', 'status': 'in_progress'}, headers={'Idempotency-Key': 'patch-2'}).json()

    # Simulate fresh cold-restarted owner/client
    fresh_client = _research_ticket_client(tickets)

    # Replay create
    replay_create = fresh_client.post('/api/v1/research/tickets', json=TICKET_BODY, headers={'Idempotency-Key': 'create'}).json()
    assert replay_create['aggregate_version'] == 1
    assert replay_create['title'] == TICKET_BODY['title']
    assert replay_create['status'] == 'open'
    assert replay_create['event_id'] == replay_create['receipt']['event_id']
    assert replay_create == created

    # Replay patch 1
    replay_patch1 = fresh_client.patch(path, json={'title': 'patch-1'}, headers={'Idempotency-Key': 'patch-1'}).json()
    assert replay_patch1['aggregate_version'] == 2
    assert replay_patch1['title'] == 'patch-1'
    assert replay_patch1['event_id'] == replay_patch1['receipt']['event_id']
    assert replay_patch1 == patch1


def test_ticket_get_detail_and_list_rejects_foreign_tenant_read_disclosure():
    tickets = CASStore()
    identity_a = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["operator"])
    client_a = _research_ticket_client(tickets, identity=identity_a)
    created = client_a.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "create-a"})
    assert created.status_code == 200, created.text
    ticket_id = created.json()["ticket_id"]

    # Same-tenant read detail and list succeed
    detail_a = client_a.get(f"/api/v1/research/tickets/{ticket_id}")
    assert detail_a.status_code == 200
    assert detail_a.json()["ticket_id"] == ticket_id
    listing_a = client_a.get("/api/v1/research/tickets")
    assert listing_a.status_code == 200
    assert ticket_id in [row.get("ticket_id") for row in listing_a.json().get("data", [])]

    # Fresh client / router instance with foreign tenant B
    identity_b = SimpleNamespace(operator_id="actor-b", tenant_id="tenant-b", roles=["operator"])
    client_b = _research_ticket_client(tickets, identity=identity_b)
    detail_b = client_b.get(f"/api/v1/research/tickets/{ticket_id}")
    assert detail_b.status_code in (403, 404), {"status": detail_b.status_code, "body": detail_b.json()}

    listing_b = client_b.get("/api/v1/research/tickets")
    assert listing_b.status_code == 200
    listed_b_ids = [row.get("ticket_id") for row in listing_b.json().get("data", [])]
    assert ticket_id not in listed_b_ids
    assert listing_b.json().get("page_info", {}).get("total") == 0


def test_ticket_list_pagination_and_count_strictly_tenant_filtered():
    tickets = CASStore()
    identity_a = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["operator"])
    client_a = _research_ticket_client(tickets, identity=identity_a)
    for idx in range(3):
        body = {**TICKET_BODY, "title": f"Ticket A-{idx}"}
        res = client_a.post("/api/v1/research/tickets", json=body, headers={"Idempotency-Key": f"tkt-a-{idx}"})
        assert res.status_code == 200

    identity_b = SimpleNamespace(operator_id="actor-b", tenant_id="tenant-b", roles=["operator"])
    client_b = _research_ticket_client(tickets, identity=identity_b)
    res_b = client_b.post("/api/v1/research/tickets", json={**TICKET_BODY, "title": "Ticket B-0"}, headers={"Idempotency-Key": "tkt-b-0"})
    assert res_b.status_code == 200
    tkt_b_id = res_b.json()["ticket_id"]

    fresh_b = _research_ticket_client(tickets, identity=identity_b)
    listing = fresh_b.get("/api/v1/research/tickets?page_size=2")
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["page_info"]["total"] == 1
    assert len(payload["data"]) == 1
    assert payload["data"][0]["ticket_id"] == tkt_b_id

    fresh_a = _research_ticket_client(tickets, identity=identity_a)
    listing_a = fresh_a.get("/api/v1/research/tickets?page_size=2")
    assert listing_a.status_code == 200
    payload_a = listing_a.json()
    assert payload_a["page_info"]["total"] == 3
    assert len(payload_a["data"]) == 2


def test_foreign_tenant_cannot_modify_ticket_via_launch():
    tickets, experiments = CASStore(), CASStore()
    identity_a = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["operator"])
    client_a = _research_ticket_client(tickets, identity=identity_a, experiments=experiments)
    r_tkt = client_a.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-a"})
    assert r_tkt.status_code == 200, r_tkt.text
    tid = r_tkt.json()["ticket_id"]

    identity_b = SimpleNamespace(operator_id="actor-b", tenant_id="tenant-b", roles=["operator"])
    client_b = _research_ticket_client(tickets, identity=identity_b, experiments=experiments)
    assert client_b.get(f"/api/v1/research/tickets/{tid}").status_code in (403, 404)

    launch_payload = {
        "ticket_id": tid,
        "experiment_name": "isolated review",
        "strategy_selector": {},
        "parameter_set": {},
        "run_config": {
            "dataset_ref": "isolated",
            "time_range": {"start_at": "2026-01-01", "end_at": "2026-01-02"},
            "execution_mode": "paper",
            "requested_by": "review",
        },
    }
    r = client_b.post("/api/v1/experiments/launch", headers={"Idempotency-Key": "foreign-launch"}, json=launch_payload)
    evidence = {"http": r.status_code, "body": r.json(), "ticket_after": tickets.get(tid)}
    assert r.status_code in (403, 404), evidence
    ticket_after = tickets.get(tid)
    assert not ticket_after.get("linked_experiments")


def test_viewer_cannot_cancel_foreign_experiment():
    tickets, experiments = CASStore(), CASStore()
    identity_a = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["operator"])
    client_a = _research_ticket_client(tickets, identity=identity_a, experiments=experiments)
    r_tkt = client_a.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-a"})
    assert r_tkt.status_code == 200
    tid = r_tkt.json()["ticket_id"]

    launch_payload = {
        "ticket_id": tid,
        "experiment_name": "launch-a",
        "strategy_selector": {},
        "parameter_set": {},
        "run_config": {
            "dataset_ref": "isolated",
            "time_range": {"start_at": "2026-01-01", "end_at": "2026-01-02"},
            "execution_mode": "paper",
            "requested_by": "review",
        },
    }
    created = client_a.post("/api/v1/experiments/launch", headers={"Idempotency-Key": "launch-a"}, json=launch_payload)
    assert created.status_code == 200, created.text
    eid = created.json()["experiment_id"]

    identity_b_viewer = SimpleNamespace(operator_id="actor-b", tenant_id="tenant-b", roles=["viewer"])
    client_b_viewer = _research_ticket_client(tickets, identity=identity_b_viewer, experiments=experiments)
    r_cancel = client_b_viewer.post(f"/api/v1/experiments/{eid}/cancel", json={"reason": "isolated auth test"}, headers={"Idempotency-Key": "cancel-b"})
    row = experiments.get(eid)
    assert r_cancel.status_code in (403, 404) and row["status"] == "queued", {
        "http": r_cancel.status_code,
        "response": r_cancel.json(),
        "persisted_status": row["status"],
        "tenant": row["tenant_id"],
    }


def test_foreign_tenant_cannot_read_experiment():
    tickets, experiments = CASStore(), CASStore()
    identity_a = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["operator"])
    client_a = _research_ticket_client(tickets, identity=identity_a, experiments=experiments)
    r_tkt = client_a.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-a"})
    assert r_tkt.status_code == 200
    tid = r_tkt.json()["ticket_id"]

    launch_payload = {
        "ticket_id": tid,
        "experiment_name": "launch-a",
        "strategy_selector": {},
        "parameter_set": {},
        "run_config": {
            "dataset_ref": "isolated",
            "time_range": {"start_at": "2026-01-01", "end_at": "2026-01-02"},
            "execution_mode": "paper",
            "requested_by": "review",
        },
    }
    created = client_a.post("/api/v1/experiments/launch", headers={"Idempotency-Key": "launch-a"}, json=launch_payload)
    assert created.status_code == 200
    eid = created.json()["experiment_id"]

    identity_b = SimpleNamespace(operator_id="actor-b", tenant_id="tenant-b", roles=["viewer"])
    client_b = _research_ticket_client(tickets, identity=identity_b, experiments=experiments)
    r_read = client_b.get(f"/api/v1/experiments/{eid}")
    assert r_read.status_code in (403, 404), {"http": r_read.status_code, "response": r_read.json()}


def test_experiment_cancel_idempotent_replay_and_receipt():
    tickets, experiments = CASStore(), CASStore()
    identity_a = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["operator"])
    client_a = _research_ticket_client(tickets, identity=identity_a, experiments=experiments)
    r_tkt = client_a.post("/api/v1/research/tickets", json=TICKET_BODY, headers={"Idempotency-Key": "ticket-a"})
    assert r_tkt.status_code == 200
    tid = r_tkt.json()["ticket_id"]

    launch_payload = {
        "ticket_id": tid,
        "experiment_name": "launch-a",
        "strategy_selector": {},
        "parameter_set": {},
        "run_config": {
            "dataset_ref": "isolated",
            "time_range": {"start_at": "2026-01-01", "end_at": "2026-01-02"},
            "execution_mode": "paper",
            "requested_by": "review",
        },
    }
    created = client_a.post("/api/v1/experiments/launch", headers={"Idempotency-Key": "launch-a"}, json=launch_payload)
    assert created.status_code == 200
    eid = created.json()["experiment_id"]

    cancel1 = client_a.post(f"/api/v1/experiments/{eid}/cancel", json={"reason": "stop exp"}, headers={"Idempotency-Key": "cancel-key-1"})
    assert cancel1.status_code == 200
    assert cancel1.json()["status"] == "canceled"

    row1 = experiments.get(eid)
    rcpt1 = row1["cancel_receipt"]
    assert rcpt1.get("event_id"), "accepted cancellation lacks durable event identity"
    assert rcpt1.get("correlation_id"), "accepted cancellation lacks durable correlation identity"
    assert rcpt1["command_id"]
    assert rcpt1["aggregate_version"] == 2

    # Replay with same key
    cancel_replay = client_a.post(f"/api/v1/experiments/{eid}/cancel", json={"reason": "stop exp"}, headers={"Idempotency-Key": "cancel-key-1"})
    assert cancel_replay.status_code == 200
    assert cancel_replay.json()["status"] == "canceled"
    assert cancel_replay.json()["completed_at"] == cancel1.json()["completed_at"]

    row_replay = experiments.get(eid)
    rcpt_replay = row_replay["cancel_receipt"]
    assert rcpt_replay["event_id"] == rcpt1["event_id"]
    assert rcpt_replay["correlation_id"] == rcpt1["correlation_id"]
    assert rcpt_replay["command_id"] == rcpt1["command_id"]
    assert rcpt_replay["aggregate_version"] == rcpt1["aggregate_version"]


class _ConcurrentBarrierStore:
    def __init__(self, rows=None, barrier=None):
        self.rows = deepcopy(rows or {})
        self.lock = Lock()
        self.barrier = barrier
        self.get_count = 0

    def get(self, key):
        with self.lock:
            result = deepcopy(self.rows.get(key))
            self.get_count += 1
            first_pair = self.get_count <= 2
        if self.barrier is not None and first_pair:
            self.barrier.wait(timeout=10)
        return result

    def put(self, key, value):
        with self.lock:
            self.rows[key] = deepcopy(value)

    def list_all(self):
        with self.lock:
            return deepcopy(list(self.rows.values()))

    def compare_and_set(self, key, expected, value, *, conn=None):
        with self.lock:
            current = deepcopy(self.rows.get(key))
            if current != expected:
                return False, current
            self.rows[key] = deepcopy(value)
            return True, deepcopy(value)


def test_concurrent_cancel_research_experiment_cas_atomic_history():
    experiment_id = "exp-concurrent-cancel"
    row = {
        "experiment_id": experiment_id,
        "tenant_id": "tenant-a",
        "status": "queued",
        "is_committed": True,
        "aggregate_version": 1,
        "ticket_id": "ticket-a",
        "experiment_name": "review",
        "command_history": [],
    }
    experiments = _ConcurrentBarrierStore({experiment_id: row}, Barrier(2))
    owner = ResearchWriteOwner(
        tickets_store=_ConcurrentBarrierStore(),
        experiments_store=experiments,
        notes_store=_ConcurrentBarrierStore(),
    )

    def cancel(key):
        return owner.cancel_research_experiment(
            experiment_id,
            tenant_id="tenant-a",
            actor_id="actor-a",
            idempotency_key=key,
            request_hash=key,
            command_id="cmd-" + key,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(cancel, "cancel-a")
        b = pool.submit(cancel, "cancel-b")
        results = [a.result(timeout=15), b.result(timeout=15)]

    persisted = experiments.rows[experiment_id]
    successful_results = [r for r in results if r is not None]
    assert len(successful_results) >= 1
    assert len(successful_results) <= len(persisted.get("command_history") or []), (
        "accepted cancellation receipt lost from durable command history"
    )
    recorded_commands = [c["command_id"] for c in persisted.get("command_history") or []]
    for r in successful_results:
        assert r["cancel_receipt"]["command_id"] in recorded_commands


def _composed_read_surface_research_client(tickets, experiments, notes=None, tenant="tenant", actor="actor", roles=("admin", "operator")):
    owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=notes or AtomicIO())
    port = DefaultResearchKnowledgeSourcePort(research_write_owner=owner)
    reads = ReadSurfacePorts(research_knowledge_source=port)
    app = FastAPI()
    identity = SimpleNamespace(operator_id=actor, tenant_id=tenant, roles=list(roles))
    app.include_router(
        create_research_router(
            read_surface=reads,
            extract_identity=lambda auth: identity,
            require_read_role=lambda i: None,
            require_operator_role=lambda i: None,
            bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
            utc_now=lambda: "2026-09-28T00:00:00Z",
        )
    )
    return TestClient(app, raise_server_exceptions=False), owner, experiments


def test_composed_read_surface_mounted_create_restart_and_conflict():
    tickets, experiments = CASStore(), CASStore()
    client, owner, _ = _composed_read_surface_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")
    body = {"name": "composed-exp", "ticket_id": ""}
    headers = {"Idempotency-Key": "composed-create-key-1"}

    # 1. First create returns 201
    first = client.post("/bff/experiments", json=body, headers=headers)
    assert first.status_code == 201, first.text
    eid = first.json()["experiment_id"]
    cmd_id = first.json()["command_id"]

    # 2. Restarted client with identical payload replayed returns 201 with same experiment and receipt
    client_restart, _, _ = _composed_read_surface_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")
    replay = client_restart.post("/bff/experiments", json=body, headers=headers)
    assert replay.status_code == 201, replay.text
    assert replay.json()["experiment_id"] == eid
    assert replay.json()["command_id"] == cmd_id

    # 3. Conflicting payload with same key returns 409
    conflict_body = {"name": "different-name", "ticket_id": ""}
    conflict = client.post("/bff/experiments", json=conflict_body, headers=headers)
    assert conflict.status_code == 409, conflict.text

    # 4. Durable store has valid receipt
    row = experiments.get(eid)
    assert row is not None
    assert row["tenant_id"] == "tenant-a"
    assert row["actor_id"] == "actor-a"
    assert row["idempotency_key"] == "composed-create-key-1"
    assert row["receipt"]["owner"] == "research"
    assert row["receipt"]["status"] == "queued"


def test_composed_read_surface_mounted_launch_restart_and_conflict():
    tickets, experiments = CASStore(), CASStore()
    tickets.put("ticket-1", {"ticket_id": "ticket-1", "tenant_id": "tenant-a", "linked_experiments": []})
    client, owner, _ = _composed_read_surface_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")
    payload = {
        "ticket_id": "ticket-1",
        "experiment_name": "composed-launch-exp",
        "strategy_selector": {},
        "parameter_set": {},
        "run_config": {
            "dataset_ref": "isolated",
            "time_range": {"start_at": "2026-01-01", "end_at": "2026-01-02"},
            "execution_mode": "paper",
            "requested_by": "actor-a",
        },
        "launch_context": {},
    }
    headers = {"Idempotency-Key": "composed-launch-key-1"}

    # 1. Launch returns 200
    first = client.post("/api/v1/experiments/launch", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    eid = first.json()["experiment_id"]

    # 2. Restarted client with same payload returns 200 and same experiment
    client_restart, _, _ = _composed_read_surface_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")
    replay = client_restart.post("/api/v1/experiments/launch", json=payload, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["experiment_id"] == eid

    # 3. Conflicting payload with same key returns 409
    conflict_payload = dict(payload, experiment_name="conflict-launch-name")
    conflict = client.post("/api/v1/experiments/launch", json=conflict_payload, headers=headers)
    assert conflict.status_code == 409, conflict.text

    # 4. Check durable row
    row = experiments.get(eid)
    assert row["tenant_id"] == "tenant-a"
    assert row["actor_id"] == "actor-a"
    assert row["idempotency_key"] == "composed-launch-key-1"


def test_composed_read_surface_mounted_cancel_restart_and_conflict():
    tickets, experiments = CASStore(), CASStore()
    client, owner, _ = _composed_read_surface_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")

    created = owner.create_research_experiment(
        ticket_id="",
        experiment_name="cancel-exp",
        strategy_selector={},
        parameter_set={},
        run_config={},
        launch_context={},
        tenant_id="tenant-a",
        actor_id="actor-a",
        idempotency_key="create-for-cancel",
        request_hash="hash-1",
    )
    eid = created["experiment_id"]
    cancel_path = f"/api/v1/experiments/{eid}/cancel"
    cancel_headers = {"Idempotency-Key": "cancel-key-1"}
    cancel_body = {"reason": "operator requested cancel"}

    # 1. First cancel returns 200
    first = client.post(cancel_path, json=cancel_body, headers=cancel_headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "canceled"
    completed_at = first.json()["completed_at"]

    # 2. Replay with restarted client returns 200 with same status and completed_at
    client_restart, _, _ = _composed_read_surface_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")
    replay = client_restart.post(cancel_path, json=cancel_body, headers=cancel_headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["status"] == "canceled"
    assert replay.json()["completed_at"] == completed_at

    # 3. Conflicting cancel payload with same key returns 409
    conflict = client.post(cancel_path, json={"reason": "different reason"}, headers=cancel_headers)
    assert conflict.status_code == 409, conflict.text

    # 4. Different key on already canceled experiment returns 409 (terminal state)
    diff_key = client.post(cancel_path, json={"reason": "new attempt"}, headers={"Idempotency-Key": "other-key"})
    assert diff_key.status_code == 409, diff_key.text

    # 5. Check durable cancel receipt
    row = experiments.get(eid)
    receipt = row.get("cancel_receipt", {})
    assert receipt["actor_id"] == "actor-a"
    assert receipt["tenant_id"] == "tenant-a"
    assert receipt["idempotency_key"] == "cancel-key-1"
    assert receipt["request_hash"] is not None
    assert receipt["status"] == "committed"
    assert receipt["owner"] == "ResearchWriteOwner"
    assert receipt["command"] == "CancelResearchExperiment"
    assert receipt["aggregate_id"] == eid
    assert receipt["aggregate_version"] == 2


def test_composed_read_surface_cross_tenant_isolation():
    tickets, experiments = CASStore(), CASStore()
    client_a, owner, _ = _composed_read_surface_research_client(tickets, experiments, tenant="tenant-a", actor="actor-a")
    client_b, _, _ = _composed_read_surface_research_client(tickets, experiments, tenant="tenant-b", actor="actor-b")

    # Tenant A creates experiment
    res_a = client_a.post("/bff/experiments", json={"name": "exp-a"}, headers={"Idempotency-Key": "shared-key"})
    assert res_a.status_code == 201
    eid_a = res_a.json()["experiment_id"]

    # Tenant B cannot read Tenant A's experiment detail
    read_b = client_b.get(f"/api/v1/experiments/{eid_a}")
    assert read_b.status_code == 403

    # Tenant B cannot cancel Tenant A's experiment
    cancel_b = client_b.post(f"/api/v1/experiments/{eid_a}/cancel", json={"reason": "stop"}, headers={"Idempotency-Key": "cancel-b"})
    assert cancel_b.status_code == 403

    # Tenant B can create their own experiment using same idempotency key (tenant-scoped)
    res_b = client_b.post("/bff/experiments", json={"name": "exp-b"}, headers={"Idempotency-Key": "shared-key"})
    assert res_b.status_code == 201
    eid_b = res_b.json()["experiment_id"]
    assert eid_b != eid_a


def test_create_replay_keeps_original_receipt_after_cancel():
    tickets, experiments = CASStore(), CASStore()
    client, _, _ = _composed_read_surface_research_client(tickets, experiments)
    body = {"name": "receipt-replay-review"}
    headers = {"Idempotency-Key": "create-key"}
    created = client.post("/bff/experiments", json=body, headers=headers)
    assert created.status_code == 201, created.text
    first = created.json()
    eid = first["experiment_id"]
    canceled = client.post(f"/api/v1/experiments/{eid}/cancel", json={"reason": "review"}, headers={"Idempotency-Key": "cancel-key"})
    assert canceled.status_code == 200, canceled.text
    restarted, _, _ = _composed_read_surface_research_client(tickets, experiments)
    replay = restarted.post("/bff/experiments", json=body, headers=headers)
    assert replay.status_code == 201, replay.text
    actual = replay.json()
    assert actual["receipt"] == first["receipt"], "Create replay must preserve original create command receipt"
    assert actual["command_id"] == first["command_id"]
    row = experiments.get(eid)
    assert row["status"] == "canceled"
    assert row["aggregate_version"] == 2


def test_wiring_rejects_identity_dropping_mutation_port():
    from services.control_plane.bff.research.service import ResearchPortWiring
    calls = []
    class LegacyOwner:
        def cancel_research_experiment(self, experiment_id, *, completed_at=None):
            calls.append((experiment_id, completed_at))
            return {"experiment_id": experiment_id, "status": "canceled"}
    wiring = ResearchPortWiring(knowledge_source=LegacyOwner())
    with pytest.raises(TypeError) as exc_info:
        wiring.cancel_research_experiment("exp-1", completed_at="2026-09-28T00:00:00Z", tenant_id="tenant-a", actor_id="actor-a", idempotency_key="cancel-key", request_hash="hash")
    assert not calls, "Wiring dispatched a mutation after silently dropping tenant, actor and idempotency identity"
    assert "incompatible" in str(exc_info.value)


def test_mounted_legacy_cancel_must_fail_closed_before_mutation():
    tickets, experiments = CASStore(), CASStore()
    owner = ResearchWriteOwner(tickets_store=tickets, experiments_store=experiments, notes_store=AtomicIO())
    class LegacyCancelPort(DefaultResearchKnowledgeSourcePort):
        def cancel_research_experiment(self, experiment_id, *, completed_at=None):
            return owner.cancel_research_experiment(experiment_id, completed_at=completed_at)
    port = LegacyCancelPort(research_write_owner=owner)
    app = FastAPI()
    app.include_router(create_research_router(
        read_surface=ReadSurfacePorts(research_knowledge_source=port),
        extract_identity=lambda auth: SimpleNamespace(operator_id="actor", tenant_id="tenant", roles=["admin", "operator"]),
        require_read_role=lambda i: None, require_operator_role=lambda i: None,
        bff_error=lambda s,c,m,*a,**kw: HTTPException(s, detail=m),
        utc_now=lambda: "2026-09-28T00:00:00Z",
    ))
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/bff/experiments", json={"name": "legacy-port"}, headers={"Idempotency-Key": "create-key"})
    assert created.status_code == 201, created.text
    eid = created.json()["experiment_id"]
    result = client.post(f"/api/v1/experiments/{eid}/cancel", json={"reason": "review"}, headers={"Idempotency-Key": "cancel-key"})
    row = experiments.get(eid)
    assert result.status_code >= 500 and row["status"] == "queued", "Incompatible owner silently accepted cancellation without authenticated actor/key/hash"


def _make_mounted_experiment_action_client(tmp_path, monkeypatch, identity=None, owner=None, store=None):
    from services.control_plane.bff.command_adapters.service import _gov_bff_action_command
    from services.control_plane.bff.models import CommandType, ObjectType
    if store is None:
        store = CommandStore(str(tmp_path / "commands.jsonl"))
    main_holder = SimpleNamespace(command_store=store)
    if identity is None:
        identity = SimpleNamespace(operator_id="actor-a", tenant_id="tenant-a", roles=["admin", "operator"], claims={}, token_kind="stub")
    if owner is None:
        owner = ResearchWriteOwner(tickets_store=CASStore(), experiments_store=CASStore(), notes_store=AtomicIO())
    reads = ReadSurfacePorts(research_knowledge_source=DefaultResearchKnowledgeSourcePort(research_write_owner=owner))

    def require_op(i):
        roles = getattr(i, "roles", [])
        if "operator" not in roles and "admin" not in roles:
            raise HTTPException(403, detail="Forbidden: operator role required")

    app = FastAPI()
    app.include_router(create_research_router(
        read_surface=reads,
        extract_identity=lambda auth: identity,
        require_read_role=lambda i: None,
        require_operator_role=require_op,
        bff_error=lambda s, c, m, *a, **kw: HTTPException(s, detail=m),
        utc_now=lambda: "2026-09-28T00:00:00Z",
        submit_experiment_action=lambda entity_type, entity_id, action_id, key, ident, payload: _gov_bff_action_command(
            ObjectType.EXPERIMENT, entity_id, action_id, key, ident, payload, CommandType.EXPERIMENT_ACTION,
            command_store=main_holder.command_store,
        ),
    ))
    return TestClient(app, raise_server_exceptions=False), main_holder, identity, owner, store



def test_mounted_experiment_action_actor_scope_and_restart(tmp_path, monkeypatch):
    client, main, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    exp = client.post("/bff/experiments", json={"name": "scoped-action-test"}).json()
    path = f"/bff/experiments/{exp['experiment_id']}/actions/cancel"

    # Actor-A submits action
    res_a = client.post(path, json={"reason": "actor-a cancel"}, headers={"Idempotency-Key": "shared-action-key"})
    assert res_a.status_code == 202
    cmd_a = res_a.json()["data"]["command_id"]

    # Actor-B submits same key -> must get a distinct command_id (actor isolation)
    identity.operator_id = "actor-b"
    res_b = client.post(path, json={"reason": "actor-b cancel"}, headers={"Idempotency-Key": "shared-action-key"})
    assert res_b.status_code == 202
    cmd_b = res_b.json()["data"]["command_id"]
    assert cmd_a != cmd_b, "Distinct actors must not share or replay identical command IDs"

    # Simulate restart: new CommandStore, switch back to Actor-A
    identity.operator_id = "actor-a"
    monkeypatch.setattr(main, "command_store", CommandStore(str(tmp_path / "commands.jsonl")))
    replay_a = client.post(path, json={"reason": "actor-a cancel"}, headers={"Idempotency-Key": "shared-action-key"})
    assert replay_a.status_code == 202
    cmd_replay = replay_a.json()["data"]["command_id"]
    assert cmd_replay == cmd_a, "Restarted replay must return original durable command_id"

    # Verify CommandStore only contains 2 commands (one for actor-a, one for actor-b)
    all_cmds = main.command_store._get_all_commands()
    assert len(all_cmds) == 2
    assert {c["command_id"] for c in all_cmds} == {cmd_a, cmd_b}


def test_mounted_experiment_action_idempotency_conflict(tmp_path, monkeypatch):
    client, main, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    exp = client.post("/bff/experiments", json={"name": "conflict-action-test"}).json()
    path = f"/bff/experiments/{exp['experiment_id']}/actions/cancel"

    res1 = client.post(path, json={"reason": "initial payload"}, headers={"Idempotency-Key": "same-action-key"})
    assert res1.status_code == 202

    res_conflict = client.post(path, json={"reason": "conflicting payload"}, headers={"Idempotency-Key": "same-action-key"})
    assert res_conflict.status_code == 409
    assert "Idempotency key was already used with a different payload" in res_conflict.text


def test_mounted_experiment_action_negative_auth(tmp_path, monkeypatch):
    client, main, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    exp = client.post("/bff/experiments", json={"name": "auth-action-test"}).json()
    path = f"/bff/experiments/{exp['experiment_id']}/actions/cancel"

    identity.roles = ["viewer"]
    res_forbidden = client.post(path, json={}, headers={"Idempotency-Key": "unauth-key"})
    assert res_forbidden.status_code == 403


def test_mounted_experiment_action_failure_paths(tmp_path, monkeypatch):
    client, main, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    exp = client.post("/bff/experiments", json={"name": "failure-action-test"}).json()
    valid_path = f"/bff/experiments/{exp['experiment_id']}/actions/cancel"

    # Nonexistent experiment returns 404
    res_404 = client.post("/bff/experiments/exp-missing-99999/actions/cancel", json={}, headers={"Idempotency-Key": "key-404"})
    assert res_404.status_code == 404

    # Storage failure (OSError) on submit_command fails closed
    with patch.object(main.command_store, "submit_command", side_effect=OSError("disk full")):
        res_fail = client.post(valid_path, json={}, headers={"Idempotency-Key": "key-disk-fail"})
        assert res_fail.status_code >= 500

    # Unconfigured command store returns 503
    monkeypatch.setattr(main, "command_store", None)
    res_503 = client.post(valid_path, json={}, headers={"Idempotency-Key": "key-503"})
    assert res_503.status_code == 503


def test_mounted_experiment_action_concurrent_conflict(tmp_path, monkeypatch):
    client, main, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    exp = client.post("/bff/experiments", json={"name": "concurrent-action-test"}).json()
    path = f"/bff/experiments/{exp['experiment_id']}/actions/cancel"

    original = main.command_store.get_command_by_idempotency_key
    barrier = Barrier(2)
    lock = Lock()
    counter = {"n": 0}

    def interleaved_lookup(*args, **kwargs):
        result = original(*args, **kwargs)
        with lock:
            counter["n"] += 1
            first_pair = counter["n"] <= 2
        if first_pair:
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(main.command_store, "get_command_by_idempotency_key", interleaved_lookup)

    def submit(reason):
        return client.post(path, json={"reason": reason}, headers={"Idempotency-Key": "concurrent-key"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(submit, "reason-one")
        b = pool.submit(submit, "reason-two")
        responses = [a.result(timeout=20), b.result(timeout=20)]

    assert sorted(r.status_code for r in responses) == [202, 409], (
        f"Concurrent conflicting payloads must yield [202, 409], got {[r.status_code for r in responses]}"
    )
    all_cmds = main.command_store._get_all_commands()
    assert len(all_cmds) == 1


def test_mounted_experiment_action_processor_execution_and_restart(tmp_path, monkeypatch):
    """Processor-level mounted regression verifying end-to-end action execution and restart durability.

    Covers SD4.4 canonical receipt persistence, version increments, restart-bound
    CommandStore replay, and conflicting payload rejection across all mounted actions
    (cancel, retry, archive, invalidate).
    """
    import asyncio
    from services.control_plane.bff.command_adapters.registry import find_adapter
    from services.control_plane.bff.command_adapters.service import process_command
    from services.control_plane.bff.models import CommandStatus, CommandType
    import services.research.write_owner as rwo_mod

    client, main, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)

    # Route adapter execution to the test in-memory write owner
    monkeypatch.setattr(rwo_mod, "build_research_write_owner", lambda: owner)
    exp_adapter = find_adapter(CommandType.EXPERIMENT_ACTION)
    assert exp_adapter is not None
    monkeypatch.setattr(exp_adapter, "_research_write_owner", owner)

    # Create an initial experiment via mounted client
    exp_res = client.post("/bff/experiments", json={"name": "processor-regression-test"})
    assert exp_res.status_code == 201
    eid = exp_res.json()["experiment_id"]

    # 1. Action: cancel
    cancel_path = f"/bff/experiments/{eid}/actions/cancel"
    r_cancel = client.post(cancel_path, json={"reason": "operator cancel test"}, headers={"Idempotency-Key": "proc-cancel-key"})
    assert r_cancel.status_code == 202
    cmd_cancel_id = r_cancel.json()["data"]["command_id"]
    assert store.get_command(cmd_cancel_id)["status"] == CommandStatus.SUBMITTED

    # Process cancel command
    asyncio.run(process_command(cmd_cancel_id, command_store=store))
    cmd_cancel_rec = store.get_command(cmd_cancel_id)
    assert cmd_cancel_rec["status"] == CommandStatus.EXECUTED
    cancel_receipt = cmd_cancel_rec["result"]["domain_receipt"]
    assert cancel_receipt["command_id"] == cmd_cancel_id
    assert cancel_receipt["aggregate_id"] == eid
    assert cancel_receipt["aggregate_type"] == "ResearchExperiment"
    assert cancel_receipt["aggregate_version"] == 2
    assert cancel_receipt["owner"] == "ResearchWriteOwner"
    assert cancel_receipt["correlation_id"] == "proc-cancel-key"
    assert cancel_receipt["event_id"].startswith("evt-")
    assert cancel_receipt["committed_at"]

    # 2. Action: retry (on canceled experiment)
    retry_path = f"/bff/experiments/{eid}/actions/retry"
    r_retry = client.post(retry_path, json={}, headers={"Idempotency-Key": "proc-retry-key"})
    assert r_retry.status_code == 202
    cmd_retry_id = r_retry.json()["data"]["command_id"]
    assert store.get_command(cmd_retry_id)["status"] == CommandStatus.SUBMITTED

    # Process retry command
    asyncio.run(process_command(cmd_retry_id, command_store=store))
    cmd_retry_rec = store.get_command(cmd_retry_id)
    assert cmd_retry_rec["status"] == CommandStatus.EXECUTED
    retry_receipt = cmd_retry_rec["result"]["domain_receipt"]
    assert retry_receipt["command_id"] == cmd_retry_id
    assert retry_receipt["owner"] == "ResearchWriteOwner"
    assert retry_receipt["aggregate_type"] == "ResearchExperiment"
    assert retry_receipt["aggregate_version"] == 1
    assert retry_receipt["correlation_id"] == "proc-retry-key"
    assert retry_receipt["event_id"].startswith("evt-")
    assert retry_receipt["committed_at"]
    new_eid = cmd_retry_rec["result"].get("new_experiment_id") or (cmd_retry_rec["result"].get("authoritative_readback") or {}).get("experiment_id")
    assert new_eid and new_eid != eid

    # 3. Action: archive (on canceled experiment eid)
    archive_path = f"/bff/experiments/{eid}/actions/archive"
    r_archive = client.post(archive_path, json={}, headers={"Idempotency-Key": "proc-archive-key"})
    assert r_archive.status_code == 202
    cmd_archive_id = r_archive.json()["data"]["command_id"]
    assert store.get_command(cmd_archive_id)["status"] == CommandStatus.SUBMITTED

    # Process archive command
    asyncio.run(process_command(cmd_archive_id, command_store=store))
    cmd_archive_rec = store.get_command(cmd_archive_id)
    assert cmd_archive_rec["status"] == CommandStatus.EXECUTED
    archive_receipt = cmd_archive_rec["result"]["domain_receipt"]
    assert archive_receipt["command_id"] == cmd_archive_id
    assert archive_receipt["aggregate_id"] == eid
    assert archive_receipt["aggregate_type"] == "ResearchExperiment"
    assert archive_receipt["aggregate_version"] == 3
    assert archive_receipt["owner"] == "ResearchWriteOwner"
    assert archive_receipt["correlation_id"] == "proc-archive-key"
    assert archive_receipt["event_id"].startswith("evt-")
    assert archive_receipt["committed_at"]

    # 4. Action: invalidate (on new retry experiment new_eid, which is queued)
    inv_path = f"/bff/experiments/{new_eid}/actions/invalidate"
    r_inv = client.post(inv_path, json={"reason": "invalidate reason"}, headers={"Idempotency-Key": "proc-inv-key"})
    assert r_inv.status_code == 202
    cmd_inv_id = r_inv.json()["data"]["command_id"]
    assert store.get_command(cmd_inv_id)["status"] == CommandStatus.SUBMITTED

    # Process invalidate command
    asyncio.run(process_command(cmd_inv_id, command_store=store))
    cmd_inv_rec = store.get_command(cmd_inv_id)
    assert cmd_inv_rec["status"] == CommandStatus.EXECUTED
    inv_receipt = cmd_inv_rec["result"]["domain_receipt"]
    assert inv_receipt["command_id"] == cmd_inv_id
    assert inv_receipt["aggregate_id"] == new_eid
    assert inv_receipt["aggregate_type"] == "ResearchExperiment"
    assert inv_receipt["aggregate_version"] == 2
    assert inv_receipt["owner"] == "ResearchWriteOwner"
    assert inv_receipt["correlation_id"] == "proc-inv-key"
    assert inv_receipt["event_id"].startswith("evt-")
    assert inv_receipt["committed_at"]

    # 5. Restart persistence & Idempotent replay:
    restarted_store = CommandStore(str(tmp_path / "commands.jsonl"))
    monkeypatch.setattr(main, "command_store", restarted_store)

    # Replay cancel with identical payload
    r_replay = client.post(cancel_path, json={"reason": "operator cancel test"}, headers={"Idempotency-Key": "proc-cancel-key"})
    assert r_replay.status_code == 202
    assert r_replay.json()["data"]["command_id"] == cmd_cancel_id
    assert r_replay.json()["meta"]["idempotency"]["replayed"] is True

    # Conflicting payload with same key returns 409
    r_conflict = client.post(cancel_path, json={"reason": "conflicting reason"}, headers={"Idempotency-Key": "proc-cancel-key"})
    assert r_conflict.status_code == 409

    # Replay archive with identical payload
    r_arch_replay = client.post(archive_path, json={}, headers={"Idempotency-Key": "proc-archive-key"})
    assert r_arch_replay.status_code == 202
    assert r_arch_replay.json()["data"]["command_id"] == cmd_archive_id
    assert r_arch_replay.json()["meta"]["idempotency"]["replayed"] is True

    # Conflicting archive payload with same key returns 409
    r_arch_conflict = client.post(archive_path, json={"archived_by": "different"}, headers={"Idempotency-Key": "proc-archive-key"})
    assert r_arch_conflict.status_code == 409


def test_finalize_rejects_arbitrary_get_put_store_without_cas():
    """Verify _finalize_experiment_record fails closed on stores lacking conditional CAS authority.

    Stores lacking compare_and_set or store-level lock/rows must not be finalized via
    unconditional get/put fallbacks or private process-local locks, which could overwrite
    concurrent durable state.
    """
    from services.research.write_owner import _finalize_experiment_record

    persisted = {"exp-1": {"version": 1, "status": "queued", "is_committed": False}}

    class ArbitraryGetPutStore:
        def get(self, key):
            return deepcopy(persisted.get(key))

        def put(self, key, value):
            persisted[key] = deepcopy(value)

    store = ArbitraryGetPutStore()
    with pytest.raises(RuntimeError, match="store does not support conditional finalization"):
        _finalize_experiment_record(store, "exp-1", deepcopy(persisted["exp-1"]))

    # Must not have been modified or committed via unconditional fallback
    assert persisted["exp-1"] == {"version": 1, "status": "queued", "is_committed": False}


def test_finalize_interleaving_preserves_concurrent_cancellation_via_cas():
    """Verify CAS-capable finalization preserves concurrent durable updates under get/put interleaving.

    When an interleaving writer persists a cancellation (version 2, status 'canceled')
    between the initial snapshot read and the final commit, the CAS path detects the
    conflict and preserves the cancellation rather than overwriting it with stale state.
    """
    from services.research.write_owner import _finalize_experiment_record

    store = CASStore()
    initial_record = {"version": 1, "status": "queued", "is_committed": False}
    store.put("exp-interleaved-1", initial_record)

    # Wrap compare_and_set to simulate an interleaving concurrent cancel on the first CAS attempt
    original_cas = store.compare_and_set
    interleaved_done = Event()

    def interleaving_cas(key, expected, candidate, *, conn=None):
        if not interleaved_done.is_set():
            # Interleave concurrent durable cancellation before first CAS executes
            store.put(key, {"version": 2, "status": "canceled", "is_committed": False})
            interleaved_done.set()
        return original_cas(key, expected, candidate, conn=conn)

    store.compare_and_set = interleaving_cas

    result = _finalize_experiment_record(store, "exp-interleaved-1", deepcopy(initial_record))

    persisted = store.get("exp-interleaved-1")
    assert persisted["version"] == 2, f"Expected version 2, got {persisted}"
    assert persisted["status"] == "canceled", f"Expected status 'canceled', got {persisted}"
    assert persisted["is_committed"] is True, f"Expected is_committed=True, got {persisted}"
    assert result["version"] == 2 and result["status"] == "canceled" and result["is_committed"] is True


def test_finalize_two_owners_shared_backend_preserves_concurrent_cancellation_via_shared_cas():
    """Verify two independent store instances sharing a backend preserve concurrent updates via store-owned CAS.

    Simulates the two-owner topology: owner A attempts to finalize an experiment record,
    while owner B concurrently persists a cancellation (version 2, status 'canceled') to
    the shared backend. The store-owned atomic compare_and_set detects the conflict, and
    owner A retries against the fresh snapshot to preserve the cancellation.
    """
    from services.research.write_owner import _finalize_experiment_record

    backend = {"exp-shared-1": {"version": 1, "status": "queued", "is_committed": False}}
    backend_lock = Lock()

    class SharedBackendCASStore:
        def __init__(self, pause_event=None, resume_event=None):
            self.pause_event = pause_event
            self.resume_event = resume_event

        def get(self, key):
            with backend_lock:
                return deepcopy(backend.get(key))

        def put(self, key, value):
            with backend_lock:
                backend[key] = deepcopy(value)

        def list_all(self, *, conn=None):
            with backend_lock:
                return deepcopy(list(backend.values()))

        def compare_and_set(self, key, expected, value, *, conn=None):
            if self.pause_event and not self.pause_event.is_set():
                self.pause_event.set()
                if self.resume_event:
                    assert self.resume_event.wait(timeout=5)
            with backend_lock:
                current = backend.get(key)
                if expected is None:
                    if current is not None:
                        return False, deepcopy(current)
                elif current != expected:
                    return False, deepcopy(current)
                backend[key] = deepcopy(value)
                return True, deepcopy(value)

    read_pause = Event()
    resume_signal = Event()

    store_a = SharedBackendCASStore(pause_event=read_pause, resume_event=resume_signal)
    store_b = SharedBackendCASStore()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _finalize_experiment_record,
            store_a,
            "exp-shared-1",
            deepcopy(backend["exp-shared-1"]),
        )
        assert read_pause.wait(timeout=5)
        store_b.put("exp-shared-1", {"version": 2, "status": "canceled", "is_committed": False})
        resume_signal.set()
        final = future.result(timeout=5)

    persisted = backend["exp-shared-1"]
    assert persisted["version"] == 2, f"Expected version 2, got {persisted}"
    assert persisted["status"] == "canceled", f"Expected status 'canceled', got {persisted}"
    assert persisted["is_committed"] is True, f"Expected is_committed=True, got {persisted}"
    assert final["version"] == 2 and final["status"] == "canceled" and final["is_committed"] is True


def test_adapter_dispatch_argument_is_command_identity_all_actions(tmp_path, monkeypatch):
    """Verify direct adapter dispatcher command_id is authoritative over body params across all 4 experiment actions."""
    from services.control_plane.bff.command_adapters.experiment_adapter import ExperimentCommandAdapter

    client, _, identity, owner, _ = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: owner)

    # 1. Action: cancel
    e1 = client.post("/bff/experiments", json={"name": "identity-cancel"}).json()["experiment_id"]
    rcpt_cancel = adapter.execute(
        "trusted-cmd-cancel",
        "ExperimentAction",
        {
            "action_id": "cancel",
            "experiment_id": e1,
            "command_id": "body-cmd-cancel",
            "actor_id": identity.operator_id,
            "tenant_id": identity.tenant_id,
        },
    )
    assert rcpt_cancel["command_id"] == "trusted-cmd-cancel"
    rec1 = owner._experiments_store.get(e1)
    assert rec1["cancel_receipt"]["command_id"] == "trusted-cmd-cancel"
    assert rec1["receipt"]["command_id"] == "trusted-cmd-cancel"
    assert rec1["command_id"] == "trusted-cmd-cancel"

    # 2. Action: retry (on canceled e1)
    rcpt_retry = adapter.execute(
        "trusted-cmd-retry",
        "ExperimentAction",
        {
            "action_id": "retry",
            "experiment_id": e1,
            "command_id": "body-cmd-retry",
            "actor_id": identity.operator_id,
            "tenant_id": identity.tenant_id,
        },
    )
    assert rcpt_retry["command_id"] == "trusted-cmd-retry"
    e2 = rcpt_retry["new_experiment_id"]
    rec2 = owner._experiments_store.get(e2)
    assert rec2["retry_receipt"]["command_id"] == "trusted-cmd-retry"
    assert rec2["receipt"]["command_id"] == "trusted-cmd-retry"
    assert rec2["command_id"] == "trusted-cmd-retry"

    # 3. Action: archive (on canceled e1)
    rcpt_archive = adapter.execute(
        "trusted-cmd-archive",
        "ExperimentAction",
        {
            "action_id": "archive",
            "experiment_id": e1,
            "command_id": "body-cmd-archive",
            "actor_id": identity.operator_id,
            "tenant_id": identity.tenant_id,
        },
    )
    assert rcpt_archive["command_id"] == "trusted-cmd-archive"
    rec1_archived = owner._experiments_store.get(e1)
    assert rec1_archived["archive_receipt"]["command_id"] == "trusted-cmd-archive"
    assert rec1_archived["receipt"]["command_id"] == "trusted-cmd-archive"

    # 4. Action: invalidate (on queued e2)
    rcpt_invalidate = adapter.execute(
        "trusted-cmd-invalidate",
        "ExperimentAction",
        {
            "action_id": "invalidate",
            "experiment_id": e2,
            "command_id": "body-cmd-invalidate",
            "actor_id": identity.operator_id,
            "tenant_id": identity.tenant_id,
        },
    )
    assert rcpt_invalidate["command_id"] == "trusted-cmd-invalidate"
    rec2_invalidated = owner._experiments_store.get(e2)
    assert rec2_invalidated["invalidate_receipt"]["command_id"] == "trusted-cmd-invalidate"
    assert rec2_invalidated["receipt"]["command_id"] == "trusted-cmd-invalidate"


@pytest.mark.parametrize("action_id", ["cancel", "retry", "archive", "invalidate"])
@pytest.mark.parametrize("dispatcher_id", ["", "   ", None])
@pytest.mark.parametrize("body_id", [None, "body-command-id-attempt"])
def test_experiment_adapter_rejects_empty_whitespace_dispatcher_command_id(
    tmp_path, monkeypatch, action_id, dispatcher_id, body_id
):
    """Negative regressions: reject empty/whitespace dispatcher command_id across all experiment actions,

    both with and without body command_id, asserting no owner state or receipt changes.
    """
    from services.control_plane.bff.command_adapters.experiment_adapter import ExperimentCommandAdapter

    client, _, identity, owner, _ = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    adapter = ExperimentCommandAdapter(research_write_owner_factory=lambda: owner)

    # Prepare experiment in appropriate state for action
    eid = client.post("/bff/experiments", json={"name": f"exp-{action_id}"}).json()["experiment_id"]

    if action_id in {"retry", "archive"}:
        # Both retry and archive require experiment in terminal state (e.g. canceled)
        adapter.execute(
            "setup-cancel-cmd",
            "ExperimentAction",
            {
                "action_id": "cancel",
                "experiment_id": eid,
                "actor_id": identity.operator_id,
                "tenant_id": identity.tenant_id,
            },
        )

    # Snapshot owner state and store keys before attempting invalid dispatch
    initial_exp = deepcopy(owner._experiments_store.get(eid))
    initial_keys = set(owner._experiments_store.rows.keys())

    params = {
        "action_id": action_id,
        "experiment_id": eid,
        "actor_id": identity.operator_id,
        "tenant_id": identity.tenant_id,
    }
    if body_id is not None:
        params["command_id"] = body_id

    with pytest.raises(ValueError, match="ExperimentAction requires a non-empty dispatcher command_id"):
        adapter.execute(dispatcher_id, "ExperimentAction", params)

    # Assert zero owner state or receipt changes
    current_exp = owner._experiments_store.get(eid)
    assert current_exp == initial_exp, f"Owner experiment record mutated for {action_id}!"
    assert set(owner._experiments_store.rows.keys()) == initial_keys, f"Store keys changed for {action_id}!"
    if action_id == "cancel":
        assert current_exp["status"] == "queued"
        assert "cancel_receipt" not in current_exp
    elif action_id == "retry":
        assert current_exp["status"] == "canceled"
        assert "retry_receipt" not in current_exp
    elif action_id == "archive":
        assert current_exp.get("is_archived") is not True
        assert "archive_receipt" not in current_exp
    elif action_id == "invalidate":
        assert current_exp["status"] == "queued"
        assert "invalidate_receipt" not in current_exp


def test_mounted_retry_canonical_aggregate_matches_owner(tmp_path, monkeypatch):
    import asyncio
    from services.control_plane.bff.command_adapters.registry import find_adapter
    from services.control_plane.bff.command_adapters.service import process_command
    from services.control_plane.bff.models import CommandType

    client, holder, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    adapter = find_adapter(CommandType.EXPERIMENT_ACTION)
    monkeypatch.setattr(adapter, "_research_write_owner", owner)
    created = client.post("/bff/experiments", json={"name": "review retry"}, headers={"Idempotency-Key": "create-key"})
    assert created.status_code == 201, created.text
    eid = created.json()["experiment_id"]
    cancel = client.post(f"/bff/experiments/{eid}/actions/cancel", json={}, headers={"Idempotency-Key": "cancel-key"})
    assert cancel.status_code == 202, cancel.text
    asyncio.run(process_command(cancel.json()["data"]["command_id"], command_store=store))
    assert owner._experiments_store.get(eid)["status"] == "canceled"

    response = client.post(f"/bff/experiments/{eid}/actions/retry", json={}, headers={"Idempotency-Key": "retry-key"})
    assert response.status_code == 202, response.text
    cid = response.json()["data"]["command_id"]
    asyncio.run(process_command(cid, command_store=store))
    record = store.get_command(cid)
    assert record["status"] == "executed", record
    result = record["result"]
    child = owner._experiments_store.get(result["new_experiment_id"])
    assert result["aggregate_id"] == child["retry_receipt"]["aggregate_id"], result
    assert result["aggregate_id"] == result["new_experiment_id"]
    assert result["previous_experiment_id"] == eid
    assert result["target_experiment_id"] == eid


def test_mounted_retry_key_collision_terminates_without_allocation_loop(tmp_path, monkeypatch):
    import asyncio
    from services.control_plane.bff.command_adapters.registry import find_adapter
    from services.control_plane.bff.command_adapters.service import process_command
    from services.control_plane.bff.models import CommandType
    import services.research.write_owner as rwo

    client, holder, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    adapter = find_adapter(CommandType.EXPERIMENT_ACTION)
    monkeypatch.setattr(adapter, "_research_write_owner", owner)
    created = client.post("/bff/experiments", json={"name": "review retry"}, headers={"Idempotency-Key": "shared-create-retry-key"})
    assert created.status_code == 201, created.text
    eid = created.json()["experiment_id"]
    cancel = client.post(f"/bff/experiments/{eid}/actions/cancel", json={}, headers={"Idempotency-Key": "cancel-key"})
    assert cancel.status_code == 202, cancel.text
    asyncio.run(process_command(cancel.json()["data"]["command_id"], command_store=store))
    assert owner._experiments_store.get(eid)["status"] == "canceled"

    response = client.post(f"/bff/experiments/{eid}/actions/retry", json={}, headers={"Idempotency-Key": "shared-create-retry-key"})
    assert response.status_code == 202, response.text
    cid = response.json()["data"]["command_id"]
    original = rwo._atomic_insert_record
    collisions = []

    def bounded_insert(*args, **kwargs):
        if len(collisions) >= 5:
            raise RuntimeError("review guard stopped unbounded idempotency allocation loop")
        result = original(*args, **kwargs)
        collisions.append((args[1], result[0], (result[1] or {}).get("experiment_id")))
        return result

    monkeypatch.setattr(rwo, "_atomic_insert_record", bounded_insert)
    asyncio.run(process_command(cid, command_store=store))
    assert len(collisions) < 5, {"collisions": collisions, "command": store.get_command(cid)}
    cmd_record = store.get_command(cid)
    assert cmd_record["status"] == "failed"
    assert cmd_record["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("mutate_child", [False, True])
def test_mounted_retry_crash_recovery_preserves_canonical_owner_receipt(tmp_path, monkeypatch, mutate_child):
    import asyncio
    from services.control_plane.bff.command_adapters.registry import find_adapter
    from services.control_plane.bff.command_adapters.service import process_command
    from services.control_plane.bff.models import CommandType, CommandStatus
    from services.research.write_owner import ResearchWriteOwner

    client, holder, identity, owner, store = _make_mounted_experiment_action_client(tmp_path, monkeypatch)
    adapter = find_adapter(CommandType.EXPERIMENT_ACTION)
    monkeypatch.setattr(adapter, "_research_write_owner", owner)
    created = client.post("/bff/experiments", json={"name": "review recovery"}, headers={"Idempotency-Key": "create"})
    assert created.status_code == 201, created.text
    parent = created.json()["experiment_id"]
    cancel = client.post(f"/bff/experiments/{parent}/actions/cancel", json={}, headers={"Idempotency-Key": "cancel-parent"})
    assert cancel.status_code == 202, cancel.text
    asyncio.run(process_command(cancel.json()["data"]["command_id"], command_store=store))
    response = client.post(f"/bff/experiments/{parent}/actions/retry", json={}, headers={"Idempotency-Key": "retry-parent"})
    assert response.status_code == 202, response.text
    cid = response.json()["data"]["command_id"]
    original_update = store.update_status
    captured = {}

    def fail_terminal(command_id, status, **kwargs):
        if command_id == cid and status == CommandStatus.EXECUTED:
            captured.update(deepcopy(kwargs["result"]))
            raise OSError("review injected terminal receipt persistence failure")
        return original_update(command_id, status, **kwargs)

    monkeypatch.setattr(store, "update_status", fail_terminal)
    with pytest.raises(OSError, match="review injected"):
        asyncio.run(process_command(cid, command_store=store))
    child = captured["new_experiment_id"]
    receipt = deepcopy(owner._experiments_store.get(child)["retry_receipt"])
    restarted_owner = ResearchWriteOwner(tickets_store=owner._tickets_store, experiments_store=owner._experiments_store, notes_store=owner._notes_store)
    monkeypatch.setattr(adapter, "_research_write_owner", restarted_owner)
    if mutate_child:
        restarted_owner.cancel_research_experiment(child, actor_id=identity.operator_id, tenant_id=identity.tenant_id,
            idempotency_key="cancel-child", request_hash="cancel-child-hash", command_id="cmd-cancel-child")
    restarted_store = CommandStore(str(tmp_path / "commands.jsonl"))
    asyncio.run(process_command(cid, command_store=restarted_store))
    record = restarted_store.get_command(cid)
    assert record["status"] == CommandStatus.EXECUTED, record
    result = record["result"]
    keys = ("command_id", "aggregate_type", "aggregate_id", "aggregate_version", "event_id", "correlation_id", "owner", "committed_at", "command")
    expected = {k: receipt.get(k) for k in keys}
    actual = {k: result["domain_receipt"].get(k) for k in keys}
    assert actual == expected
    assert result["event_id"] == receipt["event_id"]
    assert result["aggregate_type"] == "ResearchExperiment"
    assert result["owner"] == "ResearchWriteOwner"
    assert result["aggregate_version"] == 1
    assert result["aggregate_id"] == child
    assert result["domain_receipt"]["command"] == "RetryResearchExperiment"










