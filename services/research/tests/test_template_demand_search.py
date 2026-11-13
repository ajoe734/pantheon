"""Demand -> registered template -> real §10 gate on recorded official TWSE OHLCV."""
from __future__ import annotations

import copy
import hashlib
import json
import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.registry.alpha_template import (
    build_alpha_template_registry_payload, evaluate_alpha_template, load_library_templates,
)
from services.registry.factor_rule import evaluate_factor_expression
from services.registry.strategy_artifact import validate_strategy_artifact
from services.research.alpha_replication.shared_stores import SharedBacktestRunStore, SharedReplicationGate
from services.research.research_demand import ResearchDemandStore
from services.research.template_demand_search import TemplateDemandSearch, compile_template, inputs_available
from services.research.tests.test_research_orchestrator_http_service import _load_service_module

FIXTURE = Path(__file__).with_name('fixtures') / 'template_demand_tw_bars.json'
BODY = dict(persona_id='persona-template', mandate='TW equity', missing_datasets=[], tried_families=[],
            short_measure='effective_bets', pool_version=0)


@pytest.fixture
def configured_owners(monkeypatch):
    # Local transport fixtures only; never sent to hosted services.
    for name in ('RESEARCH_PERSONA_SERVICE_TOKEN', 'RESEARCH_REGISTRY_SERVICE_TOKEN'):
        monkeypatch.delenv(name + '_FILE', raising=False)
        monkeypatch.setenv(name, 'local-owner-recording-token')


def recorded():
    return json.loads(FIXTURE.read_text())['dataset']


def entries(*ids):
    result = []
    for t in load_library_templates():
        if ids and t['template_id'] not in ids:
            continue
        rid, payload = build_alpha_template_registry_payload(t)
        result.append({'entry': {'registry_id': rid, 'artifact_type': 'alpha_template', 'owner_tenant': '__builtin__',
                                 'checksum': payload.checksum, 'metadata': payload.metadata}})
    return result


def setup_search(tmp_path, *, templates=None, family_budget=500, run_budget=50, gate=None):
    store = ResearchDemandStore(tmp_path / 'demands.json')
    demand = store.open('tenant-a', BODY)
    gate = gate or SharedReplicationGate(trial_counter_path=tmp_path / 'trials.json',
                                         backtest_run_store=SharedBacktestRunStore(tmp_path / 'runs.json'))
    context = {'dataset': recorded(), 'universe': ['2330'],
               'templates': templates if templates is not None else entries('qlib_alpha158:KMID')}
    search = TemplateDemandSearch(store=store, gate=gate, context_loader=lambda d: context,
                                  lock_path=tmp_path / 'search.lock', family_budget=family_budget, run_budget=run_budget)
    return search, store, demand, context


def test_fixture_is_exactly_derived_from_recorded_ohlcv_without_synthetic_bars():
    fx = json.loads(FIXTURE.read_text())
    source = Path(fx['provenance']['source_fixture'])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == fx['provenance']['source_fixture_sha256']
    expected = []
    for recording in json.loads(source.read_text())['provenance']['recordings']:
        if 'STOCK_DAY' not in recording['url']:
            continue
        symbol = recording['url'].split('stockNo=')[1]
        if symbol not in ('2330', '0050'):
            continue
        assert recording['url'].startswith('https://www.twse.com.tw/')
        for row in recording['rows']:
            if any(row[i] in ('', '--') for i in (1, 3, 4, 5, 6)):
                continue
            year, month, day = map(int, row[0].split('/'))
            date = f'{year+1911:04}-{month:02}-{day:02}'
            o, h, l, c, v = [float(row[i].replace(',', '')) for i in (3, 4, 5, 6, 1)]
            if symbol == '0050' and date < '2025-06-18':
                o, h, l, c, v = o/4, h/4, l/4, c/4, v*4
            expected.append(dict(instrument=symbol+'.TW', date=date, open=o, high=h, low=l, close=c, volume=v))
    assert recorded()['records'] == sorted(expected, key=lambda r: (r['instrument'], r['date']))
    assert len(expected) == 1361


def test_compiler_preserves_every_registered_time_series_signal_on_recorded_inputs():
    dataset = recorded()
    rows = [r for r in dataset['records'] if r['instrument'] == '2330.TW']
    env = {k: [r[k] for r in rows] for k in ('open', 'high', 'low', 'close', 'volume')}
    compiled = 0
    for view in entries():
        template = view['entry']['metadata']['alpha_template']
        if template['scope'] != 'time_series' or not inputs_available(template, ['2330'], dataset):
            continue
        original = copy.deepcopy(template)
        artifact = compile_template(template, universe=['2330'], demand={'tenant_id': 't', 'demand_id': 'rd-1'},
                                    source_ref='registry:'+view['entry']['registry_id'], dataset=dataset)
        validate_strategy_artifact(artifact)
        assert template == original
        resolved = {i['name']: env[i['field']] for i in template['inputs']}
        expected = evaluate_alpha_template(template, resolved)
        actual = evaluate_factor_expression(artifact['strategy_logic']['signal'], resolved, artifact['parameters'])
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12), template['template_id']
        compiled += 1
    assert compiled > 100


def test_missing_universe_fields_and_vwap_are_excluded_without_imputation(tmp_path):
    search, store, demand, context = setup_search(tmp_path)
    t = context['templates'][0]['entry']['metadata']['alpha_template']
    assert not inputs_available(t, ['9999'], context['dataset'])
    missing = copy.deepcopy(context['dataset'])
    del next(r for r in missing['records'] if r['instrument'] == '2330.TW')['open']
    assert not inputs_available(t, ['2330'], missing)
    t = copy.deepcopy(t)
    t['inputs'][0]['field'] = 'vwap'
    assert not inputs_available(t, ['2330'], context['dataset'])
    context['dataset'] = missing
    result = search.run(demand)['discovery']['template_search']
    assert result['attempts'] == {} and result['excluded'][0]['reason'] == 'template_inputs_missing'
    assert not (tmp_path / 'trials.json').exists()


def test_exhausted_family_is_tried_on_demand_and_persists_across_restart(tmp_path):
    search, store, demand, context = setup_search(tmp_path, family_budget=1)
    family = 'template:qlib_alpha158:kbar'
    # Resolve the exact registered library name, never an unrelated family.
    template = context['templates'][0]['entry']['metadata']['alpha_template']
    family = f"template:{template['origin']['library']}:{template['family']}"
    search.gate.record_family_trial(family)
    result = search.run(demand)
    assert family in result['tried_families']
    assert not result['discovery']['template_search']['attempts']
    fresh = SharedReplicationGate(trial_counter_path=tmp_path / 'trials.json')
    assert fresh.get_family_total_trial_count(family) == 1
    assert result['status'] == 'open'


def test_template_receipts_survive_external_discovery_updates_and_are_tenant_scoped(tmp_path):
    search, store, demand, _ = setup_search(tmp_path)
    store.record_template_search('tenant-a', demand['demand_id'], {'attempts': {'a': {'status': 'admitted'}}},
                                 tried_families=['template:kbar'])
    store.record_discovery('tenant-a', demand['demand_id'], {'run_at': '2026-11-13T00:00:00Z', 'actions': []})
    row = store.list('tenant-a')[0]
    assert row['discovery']['template_search']['attempts']['a']['status'] == 'admitted'
    assert row['tried_families'] == ['template:kbar']
    with pytest.raises(ValueError, match='not found'):
        store.record_template_search('other-tenant', demand['demand_id'], {})


def test_unconfigured_authority_fails_closed_without_gate_or_market_fallback(tmp_path):
    search, store, demand, context = setup_search(tmp_path)
    context['dataset'] = {}
    result = search.run(demand)
    assert 'governed_market_data_missing' in result['discovery']['template_search']['reason']
    assert not result['discovery']['template_search']['attempts']
    assert not (tmp_path / 'runs.json').exists()


@pytest.mark.parametrize('field,value,reason', [('owner_tenant', 'tenant-other', 'template_tenant_mismatch'),
                                              ('checksum', 'sha256:' + '0'*64, 'template_checksum_mismatch')])
def test_registry_binding_mismatch_cannot_consume_a_trial(tmp_path, field, value, reason):
    search, store, demand, context = setup_search(tmp_path)
    context['templates'][0]['entry'][field] = value
    row = search.run(demand)
    result = row['discovery']['template_search']
    assert not result['attempts'] and result['excluded'][0]['reason'] == reason
    assert not (tmp_path / 'trials.json').exists()


@pytest.mark.parametrize('run_budget', [1, 50])
def test_passing_candidates_are_durable_linked_untested_and_each_family_is_counted(tmp_path, run_budget):
    """A gate double isolates receipt handling; real numerical admission is tested below."""
    class Gate:
        def __init__(self):
            self.counts = {}
        def get_family_total_trial_count(self, family):
            return self.counts.get(family, 0)
        def record_family_trial(self, family):
            self.counts[family] = self.counts.get(family, 0) + 1
        def evaluate_candidate(self, request):
            family = request.research_handoff['family_id']
            self.record_family_trial(family)
            return SimpleNamespace(passed=True, admission_status=SimpleNamespace(value='admitted'),
                                   to_dict=lambda: {'candidate_id': request.candidate_id})
    gate = Gate()
    search, store, demand, context = setup_search(tmp_path, gate=gate, family_budget=1, run_budget=run_budget,
                                                 templates=entries('qlib_alpha158:KMID', 'qlib_alpha158:ROC5'))
    row = search.run(demand)
    assert len(row['discovery']['template_search']['attempts']) == min(run_budget, 2)
    row = search.run(demand)
    receipts = row['discovery']['template_search']['attempts']
    assert len(receipts) == 2 and len(gate.counts) == 2
    for aid, receipt in receipts.items():
        assert receipt['family_trial_count'] == 1
        assert receipt['demand_ref'] == demand['demand_id']
        assert receipt['mechanism_status'] == 'mechanism_untested' and receipt['mechanism_support'] is None
        assert receipt['artifact']['artifact_id'] == aid and receipt['paper_only']
        assert receipt['family_id'] in row['tried_families']
    search.run(demand)
    assert all(v == 1 for v in gate.counts.values())
    assert ResearchDemandStore(store.path).list('tenant-a')[0]['discovery']['template_search']['attempts'] == receipts


def test_real_persona_reconcile_entry_drives_compilation_and_real_backtest_gate(tmp_path, monkeypatch, configured_owners):
    """Real cycle HTTP port, research route, stores, compiler, interpreter and default gate.

    Only the owner reads and HTTP transport are replaced by recorded responses.
    No gate decisions, metrics, approvals, members or market bars are fabricated.
    """
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts
    from services.persona import pool_autonomy_cycle as cycle
    from services.research.tests.test_persona_demand_routes_auth import (
        SECRET, ISSUER, AUDIENCE, TENANT, _cycle_headers,
    )
    from services.research.vectorbt.adapter import vectorbt_adapter as adapter
    for key, value in {'AUTH_MODE': 'strict', 'JWT_SECRET': SECRET,
                       'JWT_ISSUER': ISSUER, 'JWT_AUDIENCE': AUDIENCE,
                       'MFA_REQUIRED': 'false'}.items():
        monkeypatch.setenv('PANTHEON_RESEARCH_' + key, value)
    module = _load_service_module(data_dir=str(tmp_path / 'research'))
    monkeypatch.setattr(module, 'ALPHA_REPLICATION_DATA_DIR', tmp_path / 'alpha')
    monkeypatch.setenv('RESEARCH_REPLICATION_TRIAL_COUNTER_PATH', str(tmp_path / 'trials.json'))
    owner_calls = []
    entered, release = threading.Event(), threading.Event()

    def owners(**kw):
        owner_calls.append(kw)
        assert kw['tenant_id'] == TENANT
        if kw['service'] == 'persona':
            entered.set()
            assert release.wait(90), 'test did not release the slow owner'
            return {'tenant_id': TENANT, 'persona_id': BODY['persona_id'],
                    'metadata': {'market_scope': {'symbols': ['2330']}}}
        return entries('qlib_alpha158:KMID')

    monkeypatch.setattr(module, '_retrain_call', owners)
    monkeypatch.setattr(module, '_RETRAIN_DATASET_LOADER', lambda *a: recorded())
    def forbidden(*a, **kw):
        raise AssertionError('synthetic market data / fallback artifact used')
    monkeypatch.setattr(adapter, 'build_governed_tw_replication_dataset', forbidden)
    monkeypatch.setattr(adapter, 'build_canonical_tw_strategy_artifact', forbidden)
    client = TestClient(module.app)
    persona = {'tenant_id': TENANT, 'persona_id': BODY['persona_id']}
    token = _cycle_headers()['Authorization'].removeprefix('Bearer ')
    timeouts = []

    def transport(request, timeout):
        timeouts.append(timeout)
        response = client.request(request.method, request.full_url, headers=dict(request.header_items()),
                                  content=request.data)
        assert response.status_code in (200, 201), response.text
        return io.BytesIO(response.content)

    monkeypatch.setattr(cycle, 'urlopen', transport)
    ports = HttpPoolCyclePorts(env={'PERSONA_RESEARCH_SERVICE_TOKEN': token,
                                    'RESEARCH_API_URL': 'http://testserver'})
    demand = ports.open_demand(persona, BODY)
    started = time.monotonic()
    try:
        response_row = ports.reconcile_demands(persona, [])[0]
        assert time.monotonic() - started < 15
        assert entered.wait(5), 'existing Research worker did not start'
        assert 'template_search' not in (response_row.get('discovery') or {})
        assert not (tmp_path / 'alpha' / 'backtest_runs.json').exists()
        # Even with a blocked owner, the authenticated normal caller returns
        # and a concurrent cycle reuses the one durable active run.
        ports.reconcile_demands(persona, [])
        assert len(module.store.list_runs()) == 1
        run_id = module.store.list_runs()[0]['run_id']
        worker = next(t for t in threading.enumerate() if t.name == 'retrain-executor-' + run_id)
    finally:
        release.set()
    worker.join(100)
    assert not worker.is_alive(), 'real gate worker failed to terminate'
    run = module.get_run(run_id)
    assert run['status'] == 'completed', run
    assert run['adapter'] == 'template_demand_gate'
    assert module.store.get_task(run['task_id'])['status'] == 'completed'
    row = module.research_demand_store.list(TENANT)[0]
    attempts = row['discovery']['template_search']['attempts']
    assert len(attempts) == 1, row
    receipt = next(iter(attempts.values()))
    assert receipt['demand_ref'] == demand['demand_id'] and receipt['paper_only']
    bt = next(r for r in receipt['gate']['results'] if r['criterion_id'] == 'real_backtest_validation')
    assert 'Check failed with error' not in bt['evidence'], bt
    assert bt['details']['status'] in ('passed', 'failed'), bt
    assert bt['details']['history_check']['sufficient']
    assert bt['details']['history_check']['calendar_days'] >= 730
    assert bt['details']['out_of_sample_round_trips'] >= 20
    print('Recorded default-gate result:', receipt['status'],
          'OOS Sharpe:', bt['details']['out_of_sample_sharpe_ratio'],
          'round trips:', bt['details']['out_of_sample_round_trips'])
    assert receipt['gate']['metadata']['mechanism_status'] == 'mechanism_untested'
    assert receipt['family_trial_count'] == 1
    assert (tmp_path / 'alpha' / 'backtest_runs.json').exists()
    # A repeat cycle may record the outcome again, but cannot re-evaluate this candidate.
    ports.reconcile_demands(persona, [])
    for t in threading.enumerate():
        if t.name.startswith('retrain-executor-'):
            t.join(30)
            assert not t.is_alive()
    again = module.research_demand_store.list(TENANT)[0]
    assert again['discovery']['template_search']['attempts'] == attempts
    assert row['status'] == 'open'  # only a later admitted pool member satisfies a demand
    assert {c['service'] for c in owner_calls} == {'persona', 'registry'}
    assert timeouts and set(timeouts) == {15}
    assert len(module.store.list_runs()) == 2
    assert all(r['status'] == 'completed' for r in module.store.list_runs())


def test_template_runs_use_existing_resume_retry_capacity_and_cancel(tmp_path, monkeypatch, configured_owners):
    module = _load_service_module(data_dir=str(tmp_path))
    row = module.research_demand_store.open('tenant-a', BODY)
    launches = []
    monkeypatch.setattr(module, '_trigger_retrain_execution', lambda *args: launches.append(args))
    module._search_demand_templates([row])
    run = module.store.list_runs()[0]
    module._search_demand_templates([row])
    assert len(module.store.list_runs()) == 1
    module.resume_retrain_runs()
    assert {args[0] for args in launches} == {run['run_id']}
    assert module.get_run(run['run_id'])['status'] == 'queued'
    # The existing terminal and retry paths retain the demand identity.
    module._finish_run(run['run_id'], 'failed', 'recorded owner unavailable', 'research-orchestrator')
    retry = module.retry_run(run['run_id'])
    assert retry['parent_run_id'] == run['run_id']
    assert retry['parameters'] == run['parameters']
    assert launches[-1][0] == retry['run_id']
    module.cancel_run(retry['run_id'])
    count = len(launches)
    module.resume_retrain_runs()
    module._search_demand_templates([row])
    assert len(launches) == count
    assert len(module.store.list_runs()) == 2
    # A full existing Research run budget defers to the next normal cycle.
    row2 = module.research_demand_store.open('tenant-b', BODY)
    monkeypatch.setattr(module, 'MAX_ACTIVE_RUNS', 0)
    module._search_demand_templates([row2])
    assert len(module.store.list_runs()) == 2


def test_stub_parameters_cannot_invoke_template_search(tmp_path, monkeypatch):
    module = _load_service_module(data_dir=str(tmp_path))
    launches = []
    monkeypatch.setattr(module, '_trigger_retrain_execution', lambda *args: launches.append(args))
    task = module.create_task(module.CreateTaskBody(title='untrusted', objective='untrusted'))
    params = {'action_type': 'template_search', 'tenant_id': 'tenant-a',
              'persona_id': BODY['persona_id'], 'demand_id': 'forged'}
    stub = module.dispatch_run(task['task_id'], module.DispatchRunBody(parameters=params))
    assert stub['status'] == 'queued'
    rejected = module.dispatch_run(task['task_id'], module.DispatchRunBody(
        adapter='template_demand_gate', parameters=params))
    assert rejected['status'] == 'rejected'
    module.resume_retrain_runs()
    assert not launches


@pytest.mark.parametrize('cancel', [False, True])
def test_real_worker_records_owner_failure_and_respects_cancel(tmp_path, monkeypatch, configured_owners, cancel):
    module = _load_service_module(data_dir=str(tmp_path))
    monkeypatch.setattr(module, 'ALPHA_REPLICATION_DATA_DIR', tmp_path / 'alpha')
    monkeypatch.setenv('RESEARCH_REPLICATION_TRIAL_COUNTER_PATH', str(tmp_path / 'trials.json'))
    entered, release = threading.Event(), threading.Event()

    def unavailable(demand):
        entered.set()
        assert release.wait(5)
        raise ValueError('governed_market_data_missing')

    monkeypatch.setattr(module, '_template_search_context', unavailable)
    demand = module.research_demand_store.open('tenant-a', BODY)
    try:
        module._search_demand_templates([demand])
        assert entered.wait(5)
        run = module.store.list_runs()[0]
        worker = next(t for t in threading.enumerate() if t.name == 'retrain-executor-' + run['run_id'])
        if cancel:
            module.cancel_run(run['run_id'])
    finally:
        release.set()
    worker.join(5)
    assert not worker.is_alive()
    run = module.get_run(run['run_id'])
    assert run['status'] == ('canceled' if cancel else 'failed')
    assert module.store.get_task(run['task_id'])['status'] == run['status']
    if not cancel:
        assert run['failed_step'] == 'template_search'
        assert 'governed_market_data_missing' in run['rejection']['detail']


def test_unconfigured_search_keeps_collector_tasks_independent(tmp_path, monkeypatch):
    module = _load_service_module(data_dir=str(tmp_path))
    for name in ('RESEARCH_PERSONA_SERVICE_TOKEN', 'RESEARCH_REGISTRY_SERVICE_TOKEN'):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name + '_FILE', raising=False)
    row = module.research_demand_store.open('tenant-a', BODY)
    module._search_demand_templates([row])
    assert module.store.list_tasks() == module.store.list_runs() == []
    result = module.research_demand_store.list('tenant-a')[0]['discovery']['template_search']
    assert result['reason'].startswith('template_search_unavailable:owner_credential_unconfigured:')
    assert result['attempts'] == {}
