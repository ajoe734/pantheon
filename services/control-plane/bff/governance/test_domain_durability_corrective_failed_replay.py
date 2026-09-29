"""Mounted-route replay of owner-denied/unavailable persona commands (durability corrective)."""
import asyncio
import pytest
from fastapi.testclient import TestClient
from services.control_plane.bff.tests.test_command_adapters_router import _WrapperParityHarness, _WP_ROLES
from services.control_plane.bff.governance.test_domain_durability_corrective_p1_target_owner_tenant import _OwnerTransport
from services.control_plane.bff.command_adapters import persona_adapter
from services.control_plane.bff.command_adapters.service import process_command
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.ports.persona_write_owner import PersonaRegistryHttpWritePort
from services.persona.write_owner import PersistentPersonaOwner, create_app


@pytest.mark.parametrize('canonical', ['AdvanceLifecycle', 'PromoteCandidate', 'Demote'])
@pytest.mark.parametrize('mode', ['success', 'denied', 'unavailable'])
@pytest.mark.parametrize('restart', [False, True])
def test_real_owner_outcome_and_replay(tmp_path, monkeypatch, canonical, mode, restart):
    h = _WrapperParityHarness(tmp_path, monkeypatch, canonical, _WP_ROLES)
    h.client = TestClient(h.client.app, raise_server_exceptions=False)
    monkeypatch.setenv('PANTHEON_PERSONA_SERVICE_TOKEN', 'isolated-owner-token')
    monkeypatch.setenv('PANTHEON_PERSONA_SERVICE_ACTOR_ID', 'operator-bff')
    monkeypatch.setenv('PERSONA_AUTH_MODE', 'strict')
    class Verifier:
        allowed = True
        def verify_persona_lifecycle_decision(self, **kwargs):
            return self.allowed and kwargs['decision_id'] == 'approval-wp'
    verifier = Verifier()
    owner_path = tmp_path / 'owner.json'
    def new_client():
        return TestClient(create_app(owner=PersistentPersonaOwner.from_json_path(owner_path), governance_decision_verifier=verifier))
    client = new_client()
    auth = {'Authorization': 'Bearer isolated-owner-token'}
    created = client.post('/api/personas', headers=auth, json={'actor_id':'operator-bff','persona_id':h.target_id,'name':'Review','mandate':'isolated'})
    assert created.status_code == 201, created.text
    path = ['research_only', 'consultable']
    target = 'paper_owner'
    if canonical == 'Demote':
        path.append('paper_owner')
        target = 'frozen'
    for state in path:
        r = client.patch(f'/api/personas/{h.target_id}/lifecycle', headers=auth, json={'actor_id':'operator-bff','target_state':state,'governance_decision_id':'approval-wp'})
        assert r.status_code == 200, r.text
    verifier.allowed = mode != 'denied'
    transport = _OwnerTransport(client)
    port = PersonaRegistryHttpWritePort(base_url='' if mode == 'unavailable' else 'http://isolated-owner.invalid', service_token='isolated-owner-token', service_actor_id='operator-bff', opener=transport)
    monkeypatch.setattr(persona_adapter, '_persona_owner', lambda: port)
    h.issue_token('review-confirm', 'review-actor')
    headers = {'Authorization':'Bearer '+h.jwt('review-actor'), 'Idempotency-Key':'review-command', 'X-Confirm-Token':'review-confirm'}
    params = h.base_params()
    params.update(action_id=canonical, approval_decision_id='approval-wp', target_state=target)
    body = {'command':'PersonaAction', 'target':{'type':h.target_type,'id':h.target_id},'params':params,'audit_context':{'reason':'independent isolated review'}}
    accepted = h.client.post('/bff/v1/commands', headers=headers, json=body)
    assert accepted.status_code == 202, accepted.text
    rows = [r for r in h.store._get_all_commands() if r['type']=='PersonaAction']
    assert len(rows)==1
    store = CommandStore(h.command_path) if restart else h.store
    asyncio.run(process_command(rows[0]['command_id'], command_store=store))
    final = CommandStore(h.command_path).get_command(rows[0]['command_id'])
    assert final['status'] == ('executed' if mode == 'success' else 'failed'), final
    actual_state = new_client().get(f'/api/personas/{h.target_id}').json()['lifecycle_state']
    assert actual_state == (target if mode == 'success' else path[-1])
    request_count = len(transport.requests)
    if restart:
        h = _WrapperParityHarness(tmp_path, monkeypatch, canonical, _WP_ROLES)
        h.client = TestClient(h.client.app, raise_server_exceptions=False)
        monkeypatch.setattr(persona_adapter, '_persona_owner', lambda: port)
    replay = h.client.post('/bff/v1/commands', headers=headers, json=body)
    assert replay.status_code < 500, (canonical, mode, restart, final['status'], replay.status_code, replay.text)
    if mode == 'success':
        assert replay.status_code == 202, replay.text
    else:
        assert replay.status_code == 409, replay.text
        text = replay.text
        assert rows[0]['command_id'] in text
        assert 'command_terminal_failure' in text
        assert 'accepted' not in replay.json().get('status', '')
    assert len(transport.requests) == request_count
    assert len([r for r in CommandStore(h.command_path)._get_all_commands() if r['type']=='PersonaAction']) == 1
