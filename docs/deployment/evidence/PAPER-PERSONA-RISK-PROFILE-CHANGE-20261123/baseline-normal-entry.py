"""Original normal-entry behavior on clean origin/dev; local owner doubles only."""
import json
from services.control_plane.bff.test_persona_provisioning_route_security import route_harness, _headers
from services.control_plane.bff.persona_provisioning_coordinator import deterministic_provisioning_ids


def test_original_normal_operator_risk_edit_does_not_rebind_capital(route_harness):
    world = route_harness
    created = world.client.post('/bff/personas', json={'name':'Baseline isolated fixture','risk':'low'},
                                headers=_headers('fixture-operator','baseline-create'))
    assert created.status_code == 201, created.text
    pid = created.json()['data']['id']
    ledger = world.store.get_by_persona('pantheon-dev', pid)
    pool_id = deterministic_provisioning_ids(ledger).capital_pool_id
    path = '/api/capital-pools/' + pool_id
    before = world.transport.objects[('capital', path)]['metadata']['requested_risk_profile']
    patch = world.client.patch('/bff/personas/'+pid, json={'risk':'medium'},
                               headers=_headers('fixture-operator','baseline-edit'))
    assert patch.status_code == 200, patch.text
    fetched = world.client.get('/bff/personas/'+pid, headers={'Authorization':'Bearer fixture-operator:operator'})
    assert fetched.status_code == 200, fetched.text
    after = world.transport.objects[('capital', path)]['metadata']['requested_risk_profile']
    repair = world.client.post('/bff/personas/'+pid+'/provisioning/paper-capital/risk-policy',
                                json={'requested_profile':'medium','dry_run':True},
                                headers=_headers('fixture-operator','baseline-repair'))
    print(json.dumps({'baseline_commit':'bd47d73bc','local_fixture_only':True,
                      'patch_http':patch.status_code,'patch_risk':patch.json()['data']['risk'],
                      'get_http':fetched.status_code,'get_risk':fetched.json()['data']['risk'],
                      'capital_before':before,'capital_after':after,
                      'original_request':ledger.request_payload['risk'],
                      'later_tier_repair_http':repair.status_code},sort_keys=True))
    assert before == after == ledger.request_payload['risk'] == 'low'
    assert patch.json()['data']['risk'] == 'medium'
    assert repair.status_code == 409, repair.text
