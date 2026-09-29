import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from services.control_plane.bff.tests.test_command_adapters_router import _WrapperParityHarness, _WP_ROLES
from services.control_plane.bff.command_adapters import service, persona_adapter
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.ports.persona_write_owner import PersonaRegistryHttpWritePort


@pytest.mark.parametrize('wrapped', [False, True])
def test_failed_duplicate_after_precheck(tmp_path, monkeypatch, wrapped):
    h = _WrapperParityHarness(tmp_path, monkeypatch, 'AdvanceLifecycle', _WP_ROLES)
    h.client = TestClient(h.client.app, raise_server_exceptions=False)
    monkeypatch.setattr(persona_adapter, '_persona_owner', lambda: PersonaRegistryHttpWritePort(base_url=''))
    h.issue_token('race-confirm', 'review-actor')
    headers = {'Authorization': 'Bearer ' + h.jwt('review-actor'), 'Idempotency-Key': 'race-key', 'X-Confirm-Token': 'race-confirm'}
    params = h.base_params()
    params.update(action_id='AdvanceLifecycle', approval_decision_id='approval-wp')
    command = 'PersonaAction' if wrapped else 'AdvanceLifecycle'
    body = {'command': command, 'target': {'type': h.target_type, 'id': h.target_id}, 'params': params, 'audit_context': {'reason': 'isolated concurrent failed replay'}}
    original = service.command_runtime_auth_context
    injected = False
    evidence = {}

    def competing_request():
        response = h.client.post('/bff/v1/commands', headers=headers, json=body)
        assert response.status_code == 202, response.text
        rows = [r for r in h.store._get_all_commands() if r['type'] == command]
        assert len(rows) == 1
        asyncio.run(service.process_command(rows[0]['command_id'], command_store=CommandStore(h.command_path)))
        final = CommandStore(h.command_path).get_command(rows[0]['command_id'])
        assert final['status'] == 'failed', final
        evidence['original_command_id'] = final['command_id']

    def interleave(**kwargs):
        nonlocal injected
        value = original(**kwargs)
        if not injected:
            injected = True
            # Request A passed preconditions; request B with the same key
            # commits and fails before A acquires the admission lock.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(competing_request).result(timeout=20)
        return value

    monkeypatch.setattr(service, 'command_runtime_auth_context', interleave)
    raced = h.client.post('/bff/v1/commands', headers=headers, json=body)
    sequential = h.client.post('/bff/v1/commands', headers=headers, json=body)
    evidence.update(raced_status=raced.status_code, raced_body=raced.text, sequential_status=sequential.status_code)
    print(evidence)
    assert sequential.status_code == 409, sequential.text
    assert len([r for r in CommandStore(h.command_path)._get_all_commands() if r['type'] == command]) == 1
    assert raced.status_code == 409, evidence
