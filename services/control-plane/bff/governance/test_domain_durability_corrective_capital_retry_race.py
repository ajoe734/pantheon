from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.tests.test_command_adapters_router import _WrapperParityHarness, _WP_ROLES
from services.control_plane.bff.command_adapters import service
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.models import CommandStatus


@pytest.mark.parametrize('terminal', [CommandStatus.FAILED, CommandStatus.TIMEOUT])
def test_retryable_capital_duplicate_race(tmp_path, monkeypatch, terminal):
    h = _WrapperParityHarness(tmp_path, monkeypatch, 'ApprovedApply', _WP_ROLES)
    h.client = TestClient(h.client.app, raise_server_exceptions=False)
    actor = 'review-actor'
    h.issue_token('capital-race-confirm', actor)
    h.seed_rebalance_evidence(actor, 'capital-race-signature', 'approval-wp', with_approval=True)
    headers = {'Authorization': 'Bearer ' + h.jwt(actor), 'Idempotency-Key': 'capital-race-key', 'X-Confirm-Token': 'capital-race-confirm'}
    params = h.base_params()
    params.update(approval_decision_id='approval-wp', two_man_signature_id='capital-race-signature')
    body = {'command': 'ApprovedApply', 'target': {'type': h.target_type, 'id': h.target_id}, 'params': params, 'audit_context': {'reason': 'isolated reviewer fault injection; no downstream execution'}}
    original = service.command_runtime_auth_context
    injected = False
    evidence = {}

    def competing_request():
        accepted = h.client.post('/bff/v1/commands', headers=headers, json=body)
        assert accepted.status_code == 202, accepted.text
        rows = [r for r in h.store._get_all_commands() if r['type'] == 'ApprovedApply']
        assert len(rows) == 1
        # Inject a persisted retryable owner failure at the durable boundary.
        # The mounted harness background task is a no-op; no capital call runs.
        store = CommandStore(h.command_path)
        store.update_status(rows[0]['command_id'], terminal, error={'code': 'DEPENDENCY_UNAVAILABLE', 'message': 'isolated injected owner failure', 'retryable': True})
        evidence['command_id'] = rows[0]['command_id']

    def interleave(**kwargs):
        nonlocal injected
        value = original(**kwargs)
        if not injected:
            injected = True
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(competing_request).result(timeout=20)
        return value

    monkeypatch.setattr(service, 'command_runtime_auth_context', interleave)
    raced = h.client.post('/bff/v1/commands', headers=headers, json=body)
    before_control = CommandStore(h.command_path).get_command(evidence['command_id'])
    sequential = h.client.post('/bff/v1/commands', headers=headers, json=body)
    rows = [r for r in CommandStore(h.command_path)._get_all_commands() if r['type'] == 'ApprovedApply']
    evidence.update(raced_status=raced.status_code, raced_body=raced.text, raced_persisted_status=before_control['status'], sequential_status=sequential.status_code, sequential_persisted_status=rows[0]['status'], row_count=len(rows), downstream_calls=len(h.calls))

    assert sequential.status_code == 202, evidence
    assert len(rows) == 1 and not h.calls, evidence
    assert raced.status_code == 202, evidence


@pytest.mark.parametrize('terminal', [CommandStatus.FAILED, CommandStatus.TIMEOUT])
def test_retry_helper_enqueue_disabled_keeps_terminal_status(tmp_path, terminal):
    store = CommandStore(str(tmp_path / 'commands.jsonl'))
    duplicate = {'command_id': 'cmd-x', 'status': terminal.value}
    svc = service.CommandAdapterService.__new__(service.CommandAdapterService)
    calls = []
    svc._process_command_task = lambda *a, **k: calls.append(a)
    result = svc._retry_retryable_capital_duplicate(
        store=store, duplicate=duplicate, duplicate_status=terminal, enqueue=False, background_tasks=None
    )
    assert result == terminal and not calls
