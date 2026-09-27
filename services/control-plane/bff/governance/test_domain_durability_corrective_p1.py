from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from services.control_plane.bff.governance.router import create_governance_router
from services.control_plane.bff.governance.service import GovernanceService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.capital.router import create_capital_router
from services.control_plane.bff.capital.service import DefaultCapitalAuthority
from services.control_plane.bff.runtime.router import create_runtime_router
from services.control_plane.bff.runtime.service import _resolve_default_runtime_owner_port

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

