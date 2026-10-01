import importlib.util
from pathlib import Path

import pytest
from services.control_plane.bff import test_bff_promotion_review_governance as g


@pytest.mark.parametrize(('owner_state', 'action', 'target'), [('paper_owner', 'freeze_persona', 'frozen'), ('live_owner', 'retire_persona', 'retired')])
def test_mounted_ranking_drives_lifecycle_proposal(owner_state, action, target, tmp_path):
    spec = importlib.util.spec_from_file_location('review_evaluator', Path.cwd() / 'services/persona-evaluator-agent/persona_evaluator_agent.py')
    pea = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pea)
    with g._isolated_client() as (client, store, commands):
        pid = 'persona-us-equity'
        store._data['personas'][pid]['lifecycle_state'] = owner_state
        response = client.get('/bff/management/quarterly-ranking', headers=g.OPERATOR_HEADERS, params={'quarter': '2026-Q1', 'page_size': 200})
        assert response.status_code == 200, response.text
        payload = response.json()
        ranked = next(i for i in payload['data']['items'] if i['persona_id'] == pid)
        proposals = []
        def fetch(url, data=None, **kwargs):
            if 'quarterly-ranking' in url:
                return payload
            if '/structured' in url:
                inputs, _ = pea.collect_evidence('http://bff', '2026-Q1', {}, lambda *a, **k: payload)
                selected = next(i for i in inputs if i['persona_id'] == pid)
                return {'data': {'output': {'structured_data': {'recommendations': [{
                    'persona_id': pid, 'action_id': action, 'rationale': 'Provider requests freeze on cited risk evidence.',
                    'evidence_ref_ids': selected['evidence_ref_ids'],
                }]}}}}
            if '/api/governance/approvals' in url:
                proposals.append(data)
                return {'_http_status': 201}
            raise AssertionError(url)
        outcome = pea.run_once(store=pea.Store(tmp_path / 'agent.json'), bff_url='http://bff', bff_headers={},
            adapter_url='http://adapter', adapter_token='test', governance_url='http://gov', governance_token='test',
            tenant=g._PM12_ELIGIBLE_TENANT_ID, actor='evaluator', fetch=fetch, now=lambda: 1770000000.0)
        assert outcome['created'] == 1, outcome
        subject = proposals[0]['subject']
        assert (subject['from_state'], subject['to_state']) == (owner_state, target)
