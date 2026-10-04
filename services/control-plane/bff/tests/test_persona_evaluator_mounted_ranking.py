import importlib.util
from pathlib import Path

import pytest
import sys

@pytest.fixture(autouse=True)
def owner_sql(monkeypatch):
    from services.rankings.test_store import _FakeConnection, _fake_psycopg
    monkeypatch.setattr(_FakeConnection, "rows", {})
    monkeypatch.setattr(_FakeConnection, "statements", [])
    monkeypatch.setitem(sys.modules, "psycopg", _fake_psycopg())
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
                return {**data, '_http_status': 201}
            raise AssertionError(url)
        outcome = pea.run_once(store=pea.Store(tmp_path / 'agent.json'), bff_url='http://bff', bff_headers={},
            adapter_url='http://adapter', adapter_token='test', governance_url='http://gov', governance_token='test',
            tenant=g._PM12_ELIGIBLE_TENANT_ID, actor='evaluator', fetch=fetch, now=lambda: 1770000000.0)
        assert outcome['created'] == 1, outcome
        subject = proposals[0]['subject']
        assert (subject['from_state'], subject['to_state']) == (owner_state, target)


def test_mounted_state_change_invalidates_snapshot_and_preserves_reuse_and_idempotency(tmp_path):
    spec = importlib.util.spec_from_file_location('review_evaluator', Path.cwd() / 'services/persona-evaluator-agent/persona_evaluator_agent.py')
    pea = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pea)
    with g._isolated_client() as (client, store, commands):
        pid = 'persona-us-equity'
        payloads, asks, proposals = [], [], []

        def fetch(url, data=None, **kwargs):
            if 'quarterly-ranking' in url:
                response = client.get('/bff/management/quarterly-ranking', headers=g.OPERATOR_HEADERS, params={'quarter': '2026-Q1', 'page_size': 200})
                assert response.status_code == 200, response.text
                payloads.append(response.json())
                return payloads[-1]
            if '/structured' in url:
                asks.append(data)
                inputs, _ = pea.collect_evidence('http://bff', '2026-Q1', {}, lambda *a, **k: payloads[-1])
                selected = next(i for i in inputs if i['persona_id'] == pid)
                return {'data': {'output': {'structured_data': {'recommendations': [{
                    'persona_id': pid, 'action_id': 'retire_persona', 'rationale': 'Provider requests retirement.',
                    'evidence_ref_ids': selected['evidence_ref_ids'],
                }]}}}}
            if '/api/governance/approvals' in url:
                proposals.append(data)
                return {**data, '_http_status': 201}
            raise AssertionError(url)

        agent_store_path = tmp_path / 'agent.json'
        args = dict(
            store=pea.Store(agent_store_path), bff_url='http://bff', bff_headers={},
            adapter_url='http://adapter', adapter_token='test', governance_url='http://gov',
            governance_token='test', tenant=g._PM12_ELIGIBLE_TENANT_ID, actor='evaluator',
            fetch=fetch, now=lambda: 1770000000.0,
        )

        # 1. First run in paper_owner: retire_persona is invalid for paper_owner -> 0 proposals
        store._data['personas'][pid]['lifecycle_state'] = 'paper_owner'
        first = pea.run_once(**args)
        assert first['reused'] is False
        assert first['created'] == 0
        assert len(asks) == 1
        assert len(proposals) == 0

        # 2. Unchanged input: same paper_owner state -> reuse without calling provider again
        repeat = pea.run_once(**args)
        assert repeat['reused'] is True
        assert repeat['ranking_snapshot_id'] == first['ranking_snapshot_id']
        assert len(asks) == 1

        # 3. State change to live_owner: snapshot changes, provider called again, retire_persona creates 1 proposal
        store._data['personas'][pid]['lifecycle_state'] = 'live_owner'
        second = pea.run_once(**args)
        assert second['ranking_snapshot_id'] != first['ranking_snapshot_id']
        assert second['reused'] is False
        assert len(asks) == 2
        assert second['created'] == 1
        assert len(proposals) == 1
        assert proposals[0]['subject'] == {'persona_id': pid, 'from_state': 'live_owner', 'to_state': 'retired'}

        # 4. Restart / pending-request idempotency: restart evaluator with new Store on same path
        restarted_args = dict(args, store=pea.Store(agent_store_path))
        third = pea.run_once(**restarted_args)
        assert third['reused'] is True
        assert third['created'] == 0
        assert len(proposals) == 1
