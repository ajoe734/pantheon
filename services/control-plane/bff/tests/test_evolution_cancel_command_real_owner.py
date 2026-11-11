import pytest
from services.control_plane.bff.tests import test_evolution_owner_write_service_auth as support

env = support.env

@pytest.mark.parametrize('roles', [('operator',), ('admin', 'operator'), ('reviewer', 'operator')])
def test_real_cancel_admission_and_owner(env, roles):
    client, owner, did, seen, commands = env
    response = support._post(client, support._jwt(roles=roles), 'CancelMutation', did,
                             {'decision_id': did, 'note': 'independent review cleanup'}, 'review-cancel')
    _, record = support._run(commands, response)
    assert record['status'] == 'executed', {'roles': roles, 'record': record}
    assert record['result']['decision_state'] == 'canceled'
    final = owner.get(f'/api/evolution/proposals/{did}', headers={
        'Authorization': f'Bearer {support.SERVICE_TOKEN}', 'X-Tenant-Id': support.TENANT}).json()
    assert final['decision_state'] == 'canceled'

def test_other_tenant_cancel_rejected_by_real_owner(env):
    client, owner, did, seen, commands = env
    response = support._post(client, support._jwt(tenant='foreign-tenant', roles=('operator',)),
                             'CancelMutation', did, {'decision_id': did, 'note': 'review tenant isolation'}, 'review-foreign')
    if response.status_code == 202:
        _, record = support._run(commands, response)
        assert record['status'] == 'failed', record
    else:
        assert response.status_code in (403, 404), response.text
    final = owner.get(f'/api/evolution/proposals/{did}', headers={
        'Authorization': f'Bearer {support.SERVICE_TOKEN}', 'X-Tenant-Id': support.TENANT}).json()
    assert final['decision_state'] == 'proposed'
