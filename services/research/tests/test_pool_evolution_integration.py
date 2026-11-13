"""Recorded TWSE prices through the real tick, proposal, dispatch and research run.

Owner HTTP, baseline attestations and gate verdicts are controlled test ports.
This is orchestration proof, not a claim that a new strategy passed a hosted gate.
The genuine baseline/gate proof validators have separate recorded-output tests.
"""
import copy
import importlib
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from services.evolution.test_auto_consumer_dispatch import world as dispatch_world, client as evolution_client
from services.research.tests.test_evolution_gate_executor import research, world as research_world
from services.research.tests.test_pool_admission import recorded_dataset
from services.research.alpha_replication.test_evolution_executor_case_file import case_owner_server, LESSON, OWNER_URLOPEN


@pytest.fixture()
def case_owners(monkeypatch, research_world, dispatch_world):
    with case_owner_server(monkeypatch, research_world.parent, persona_id='p1') as server:
        dispatch_urlopen = urllib.request.urlopen
        def urlopen(request, *args, **kwargs):
            if request.full_url.startswith(server.endpoint.base_url):
                return OWNER_URLOPEN(request, *args, **kwargs)
            return dispatch_urlopen(request, *args, **kwargs)
        monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
        yield server


@pytest.mark.parametrize("child_fresh_passed", [True, False])
def test_recorded_bars_tick_to_research_trial_and_sixty_day_parent_removal(
    dispatch_world, research, research_world, monkeypatch, tmp_path, child_fresh_passed, case_owners,
):
    import services.evolution.auto_consumer_runtime as runtime
    import services.evolution.pool_evolution_scheduler as scheduler
    import services.evolution.approved_baselines as baselines
    from services.evolution import main as evolution_main, dispatch_worker
    from services.evolution.dispatch_receipts import ResearchPlaneAdapter
    from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks
    from services.foundation.reliable_delivery import AtomicJsonRecordStore
    from services.registry.strategy_artifact import strategy_artifact_checksum

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'control-plane' / 'persona'))
    pools = importlib.import_module('persona_strategy_pool')
    parent = research_world.parent
    parent_id = parent['artifact_id']
    tenant, pid = 'default', 'p1'
    entries = {parent_id: {'registry_id': parent_id, 'version': parent['version'],
        'artifact_state': 'approved', 'checksum': strategy_artifact_checksum(parent), 'owner_tenant': tenant,
        'approval_decision_id': 'test-owner-baseline', 'replication_success': True,
        'replication_proof': {'test_owner_attestation': True},
        'metadata': {'strategy_artifact': parent, 'persona_id': pid}}}
    # Attestation port: production still calls resolve_member_baseline with the
    # full registry-owned proof. Never modify a recorded proof to fit a child.
    def baseline(**kw):
        return {'status': 'approved', 'baseline_key': kw['artifact_ref'], 'oos_sharpe': .01,
                'oos_max_drawdown': .9, 'order_quantity': 10,
                'validated_universe': parent['parameters']['symbols'],
                'return_basis': baselines.RETURN_BASIS, 'periods_per_year': 365}
    monkeypatch.setattr(baselines, 'resolve_member_baseline', baseline)
    monkeypatch.setattr(scheduler, 'resolve_member_baseline', baseline)
    store = pools.PersonaStrategyPoolStore(AtomicJsonRecordStore(tmp_path / 'pool.json'),
        registry_reader=lambda ref, _: entries.get(ref, {}), now=lambda: '2024-09-01T00:00:00Z')
    store.append(pid, tenant, pools.AppendPoolVersionRequest(expected_version=0,
        members=[pools.PoolMember(artifact_ref=parent_id, checksum=entries[parent_id]['checksum'],
                 stage='active', risk_weight=1, baseline_ref='test-owner-baseline', joined_at='2024-08-01T00:00:00Z')],
        change_reason='recorded integration seed'), actor_id='test-owner')
    persona = {'persona_id': pid, 'tenant_id': tenant, 'lifecycle_state': 'paper_owner'}
    published = []
    dataset = recorded_dataset()
    days = sorted({r['date'] for r in dataset['records']})
    initial_days = days[:19]

    def books_for(members, selected):
        books = MemberVirtualBooks()
        for row in dataset['records']:
            if row['date'] not in selected:
                continue
            # Controlled member views on unmodified recorded exchange prices.
            # A losing parent's book and a stronger child's book exercise the
            # owner comparison without assuming profitable random mutations.
            views = {m['artifact_ref']: {'artifact_id': m['artifact_ref'], 'version': '1.0.0',
                'checksum': m['checksum'], 'underlying_count': 2, 'virtual_only': m['stage'] == 'trial',
                'view': {'direction': 'SHORT' if m['artifact_ref'] == parent_id else 'LONG', 'strength': 1}}
                for m in members if m['stage'] != 'removed'}
            books.observe(binding_id='binding-1', envelope_checksum='test-envelope',
                symbol=row['instrument'], event_time=row['date'] + 'T05:30:00Z',
                price=row['close'], source_ref=dataset['source_dataset_refs'][0], members=views, bar_frequency='1d')
        result = books.snapshot()
        for book in result:
            # The baseline attestation is separately tested. A comparable metric
            # block enables this test's scheduling; competition uses real returns.
            book['gate_basis'] = {'basis': baselines.RETURN_BASIS, 'periods_per_year': 365,
                                 'sharpe_ratio': 1, 'max_drawdown': .01}
        return result

    summary_books = books_for(store.current(pid, tenant)['members'], initial_days)
    env = {**__import__('services.evolution.test_auto_consumer_dispatch', fromlist=['ENV']).ENV,
        'AUTO_CONSUMER_TELEMETRY_URL': 'http://tel', 'AUTO_CONSUMER_TELEMETRY_TOKEN': 'telemetry-test',
        'AUTO_CONSUMER_RESEARCH_URL': 'http://research', 'AUTO_CONSUMER_RESEARCH_TOKEN': 'research-test',
        'AUTO_CONSUMER_BFF_URL': 'http://bff', 'AUTO_CONSUMER_BFF_TOKEN': 'operator-test',
        'AUTO_CONSUMER_PROPOSER_TOKEN': 'tok-evo-proposer', 'AUTO_CONSUMER_PROPOSER_ID': 'evolution-execute-proposer',
        'AUTO_CONSUMER_EVIDENCE_PATH': str(tmp_path / 'evidence.jsonl'),
        'AUTO_CONSUMER_DECAY_LEDGER_PATH': str(tmp_path / 'decay.jsonl')}
    original_transport = dispatch_world['transport']

    def transport(method, url, headers, body):
        if url == 'http://per/api/personas?lifecycle_state=paper_owner': return 200, [persona]
        if url == 'http://per/api/personas/p1': return 200, persona
        if url == 'http://per/api/personas/p1/strategy-pool': return 200, store.current(pid, tenant)
        if url.endswith('/strategy-pool/decisions'):
            return 201, store.decide(pid, tenant, pools.PoolDecisionRequest.model_validate(body), actor_id='pool-owner')
        if url.endswith('/strategy-pool/versions'):
            return 201, store.append(pid, tenant, pools.AppendPoolVersionRequest.model_validate(body), actor_id='pool-owner')
        if url.startswith('http://tel/'):
            return 200, {'summaries': [{'tenant_id': tenant, 'persona_capital_binding_id': 'pcb-discovery-1',
                'deployment_stage': 'paper', 'member_virtual_books': summary_books}]}
        if url.startswith('http://research/'): return 200, {'trials': 0, 'status': 'available'}
        if url.startswith('http://bff/'):
            published.append(body['pool_version'])
            return 200, {'data': {'status': 'published', 'pool_version': body['pool_version']}}
        if url.startswith('http://reg/api/registry/strategy-artifacts/'):
            child_id = url.split('/strategy-artifacts/')[1].split('/')[0]
            if url.endswith('/advance'):
                entries[child_id].update(artifact_state='approved', approval_decision_id=body['approval_decision_id'])
                return 200, {'entry': entries[child_id]}
            return (200, {'entry': entries[child_id]}) if child_id in entries else (404, {})
        return original_transport(method, url, headers, body)

    original_call = research._retrain_call
    def call(**kw):
        url, method = kw['url'], kw['method']
        if method == 'GET' and url.endswith('/strategy-specs/spec-1'):
            result = original_call(**kw)
            result['entry']['owner_tenant'] = tenant
            return result
        if method == 'GET' and url.endswith('/strategy-artifacts/' + parent_id):
            return {'entry': entries[parent_id]}
        if method == 'POST' and url.endswith('/api/registry/strategy-artifacts'):
            child = kw['body']['strategy_artifact']
            entries[child['artifact_id']] = {'registry_id': child['artifact_id'], 'version': child['version'],
                'artifact_state': 'candidate', 'checksum': strategy_artifact_checksum(child), 'owner_tenant': tenant,
                'replication_success': True, 'replication_proof': {'test_owner_attestation': True},
                'metadata': {'strategy_artifact': child, 'persona_id': pid}}
        return original_call(**kw)
    monkeypatch.setattr(research, '_retrain_call', call)
    monkeypatch.setattr(research, '_pool_owner', lambda _: runtime.PoolOwner(env, transport))
    monkeypatch.setattr(research, '_RETRAIN_DATASET_LOADER', lambda *_: dataset)
    # One passing gate outcome among the 50 real structural builds; all other
    # outcomes fail, exercising a bounded batch without filling the safety cap.
    from services.research.alpha_replication import shared_stores
    base_gate = shared_stores.SharedReplicationGate
    class Gate(base_gate):
        seen = 0
        def evaluate_candidate(self, request):
            research_world.passes = self.seen == 0
            self.seen += 1
            return super().evaluate_candidate(request)
    monkeypatch.setattr(shared_stores, 'SharedReplicationGate', Gate)

    research_client = TestClient(research.app)
    def http_json(method, url, *, payload, timeout):
        if (payload or {}).get('parameters', {}).get('pool_member'):
            # Controlled proposer declaration: the scheduler itself must not
            # invent a claim that a structural mutation addresses a lesson.
            payload['parameters']['pool_member']['addresses_lessons'] = {'lesson_ids': [LESSON]}
        response = research_client.request(method, url.split('http://research', 1)[1], json=payload)
        return response.status_code, response.json()
    evolution_main.receipt_registry['research'] = ResearchPlaneAdapter(api_url='http://research', http_json=http_json)
    tick = runtime.run_consumer_tick(env, transport)
    assert not tick['errors'] and not tick['schedule']['errors'], tick
    assert len(tick['approved']) == 1
    decision = evolution_client.get('/api/evolution/proposals/' + tick['approved'][0]).json()
    assert decision['action_type'] == 'mutate' and decision['risk_level'] == 'low'
    from services.evolution.test_auto_consumer_dispatch import _poll
    _poll(dispatch_world)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        runs = research.get_store().list_runs()
        if runs and runs[0]['status'] in {'completed', 'failed'}: break
        time.sleep(.05)
    assert runs[0]['status'] == 'completed', runs
    assert runs[0]['outcome'] == 'candidate_created', runs
    assert len(runs[0]['evolution_result']['children']) == 50
    pool = store.current(pid, tenant)
    trial = next(m for m in pool['members'] if m['stage'] == 'trial')
    assert trial['parent_ref'] == parent_id and trial['risk_weight'] == 0 and published
    owner = runtime.PoolOwner(env, transport)
    short_books = books_for(pool['members'], days[:60])
    with pytest.raises(RuntimeError, match='60_new_trading_days'):
        owner.compete(pool, trial, {b['artifact_id']: {**b, **b['net_of_costs']} for b in short_books})
    assert store.current(pid, tenant)['members'][0]['stage'] == 'active'
    assert store.current(pid, tenant)['members'][1]['stage'] == 'trial'
    pool = store.current(pid, tenant)
    # The book includes more than 60 distinct exchange trading days after admission.
    summary_books[:] = books_for(pool['members'], days)
    if not child_fresh_passed:
        next(b for b in summary_books if b['artifact_id'] == trial['artifact_ref'])['gate_basis']['sharpe_ratio'] = -1
    owner.compete(pool, trial, {b['artifact_id']: {**b, **b['net_of_costs']} for b in summary_books})
    final = store.current(pid, tenant)
    assert next(m for m in final['members'] if m['artifact_ref'] == parent_id)['stage'] == ('removed' if child_fresh_passed else 'active')
    assert next(m for m in final['members'] if m['artifact_ref'] == trial['artifact_ref'])['stage'] == ('active' if child_fresh_passed else 'removed')
    assert final['decision']['child_fresh_window_passed'] is child_fresh_passed
    assert persona['lifecycle_state'] == 'paper_owner'
