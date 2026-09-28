from concurrent.futures import ThreadPoolExecutor
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
from services.control_plane.bff.research.router import create_research_experiments_router
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
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




