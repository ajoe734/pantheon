import asyncio
import pytest
from fastapi.testclient import TestClient
from services.control_plane.bff.tests.test_command_adapters_router import _WrapperParityHarness, _WP_ROLES
from services.control_plane.bff.governance.test_domain_durability_corrective_p1_target_owner_tenant import _OwnerTransport, _ApprovingVerifier
from services.control_plane.bff.command_adapters import persona_adapter
from services.control_plane.bff.command_adapters.service import process_command
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.ports.persona_write_owner import PersonaRegistryHttpWritePort
from services.persona.write_owner import PersistentPersonaOwner, create_app

@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('restart', [False, True])
def test_mounted_success_keeps_canonical_owner_receipt(tmp_path, monkeypatch, wrapped, restart):
    h = _WrapperParityHarness(tmp_path, monkeypatch, 'AdvanceLifecycle', _WP_ROLES)
    monkeypatch.setenv('PANTHEON_PERSONA_SERVICE_TOKEN', 'review-isolated-token')
    monkeypatch.setenv('PANTHEON_PERSONA_SERVICE_ACTOR_ID', 'operator-bff')
    monkeypatch.setenv('PERSONA_AUTH_MODE', 'strict')
    owner_path = tmp_path / 'owner.json'
    def owner_client():
        return TestClient(create_app(owner=PersistentPersonaOwner.from_json_path(owner_path), governance_decision_verifier=_ApprovingVerifier()))
    client = owner_client()
    auth = {'Authorization': 'Bearer review-isolated-token'}
    response = client.post('/api/personas', headers=auth, json={'actor_id': 'operator-bff', 'persona_id': h.target_id, 'name': 'Isolated', 'mandate': 'review'})
    assert response.status_code == 201, response.text
    for state in ('research_only', 'consultable'):
        response = client.patch(f'/api/personas/{h.target_id}/lifecycle', headers=auth, json={'actor_id': 'operator-bff', 'target_state': state, 'governance_decision_id': 'approval-wp'})
        assert response.status_code == 200, response.text
    transport = _OwnerTransport(client)
    port = PersonaRegistryHttpWritePort(base_url='http://review-owner.invalid', service_token='review-isolated-token', service_actor_id='operator-bff', opener=transport)
    monkeypatch.setattr(persona_adapter, '_persona_owner', lambda: port)
    h.issue_token('receipt-confirm', 'review-actor')
    params = h.base_params()
    params.update(action_id='AdvanceLifecycle', approval_decision_id='approval-wp', target_state='paper_owner')
    command = 'PersonaAction' if wrapped else 'AdvanceLifecycle'
    headers = {'Authorization': 'Bearer ' + h.jwt('review-actor'), 'Idempotency-Key': 'receipt-review-key', 'X-Confirm-Token': 'receipt-confirm'}
    body = {'command': command, 'target': {'type': h.target_type, 'id': h.target_id}, 'params': params, 'audit_context': {'reason': 'independent canonical receipt verification'}}
    response = h.client.post('/bff/v1/commands', headers=headers, json=body)
    assert response.status_code == 202, response.text
    rows = [r for r in h.store._get_all_commands() if r['type'] == command]
    assert len(rows) == 1
    asyncio.run(process_command(rows[0]['command_id'], command_store=CommandStore(h.command_path)))
    assert owner_client().get(f'/api/personas/{h.target_id}').json()['lifecycle_state'] == 'paper_owner'
    if restart:
        h = _WrapperParityHarness(tmp_path, monkeypatch, 'AdvanceLifecycle', _WP_ROLES)
    persisted = CommandStore(h.command_path).get_command(rows[0]['command_id'])
    assert persisted['status'] == 'executed', persisted
    replay = h.client.post('/bff/v1/commands', headers=headers, json=body)
    assert replay.status_code == 202, replay.text
    required = {'command_id', 'aggregate_type', 'aggregate_id', 'aggregate_version', 'status', 'event_id', 'correlation_id', 'owner', 'committed_at'}
    receipt = persisted['result']
    missing = sorted(required - receipt.keys())
    assert not missing, {'wrapped': wrapped, 'restart': restart, 'missing': missing, 'result': receipt, 'foundation_receipt': persisted.get('foundation', {}).get('receipt')}
    assert required <= persisted['foundation']['receipt'].keys()
