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
                price=row['close'], open_price=row['open'], source_ref=dataset['source_dataset_refs'][0], members=views, bar_frequency='1d')
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


def _recorded_2yr_exchange_dataset() -> dict:
    import json

    fixture = Path(__file__).with_name("fixtures") / "template_demand_tw_bars.json"
    return json.loads(fixture.read_text(encoding="utf-8"))["dataset"]


def _passing_2yr_klen_artifact(dataset: dict) -> dict:
    from services.registry.alpha_template import load_library_templates
    from services.registry.strategy_artifact import validate_strategy_artifact
    from services.research.template_demand_search import compile_template

    tpl = next(t for t in load_library_templates() if t["template_id"] == "qlib_alpha158:KLEN")
    demand = {"demand_id": "dem-legacy-klen-1", "tenant_id": "default", "persona_id": "p1"}
    art = compile_template(tpl, universe=["2330"], demand=demand, source_ref="registry:tpl-klen", dataset=dataset)
    # Strip the default screen to build the legacy pre-screen single-symbol member (real screened members need >= 3 symbols).
    art["strategy_logic"].pop("screen", None)
    art["strategy_logic"]["inputs"] = [i for i in art["strategy_logic"]["inputs"] if i["name"] != "hc_default_close"]
    art["parameters"] = {k: v for k, v in art["parameters"].items() if not k.startswith("hc_default_")}
    art["mutation_surface"]["controls"] = [c for c in art["mutation_surface"]["controls"] if not c["parameter_key"].startswith("hc_default_")]
    thresh = 0.0101
    art["parameters"]["demand_zero"] = thresh
    art["strategy_logic"]["strength"] = {"op": "clip01", "args": [{"op": "div", "args": [{"ref": "signal"}, {"const_param": "demand_zero"}]}]}
    for ctrl in art["mutation_surface"]["controls"]:
        if ctrl["parameter_key"] == "demand_zero":
            ctrl.update(current_value=thresh, allowed_range={"min": 0.0001, "max": 0.05}, step=0.0001)
    validate_strategy_artifact(art)
    return art


def test_legacy_close_fill_canonical_momentum_reproduces_blockers_and_fails_requalification_on_recorded_2yr_exchange_data(
    dispatch_world, research, monkeypatch, tmp_path,
):
    import json
    import services.evolution.auto_consumer_runtime as runtime
    from services.evolution import main as evolution_main
    from services.evolution.approved_baselines import resolve_member_baseline
    from services.evolution.dispatch_receipts import ResearchPlaneAdapter
    from services.evolution.test_approved_baselines import genuine_entry
    from services.evolution.test_auto_consumer_dispatch import _poll
    from services.execution.lean_runtime.member_virtual_book import (
        FILL_CONVENTION,
        LEGACY_FILL_CONVENTION,
        idle_pool_observations,
    )
    from services.foundation.reliable_delivery import AtomicJsonRecordStore
    from services.persona.pool_autonomy_cycle import HttpPoolCyclePorts, PoolAutonomyCycle
    from services.research.alpha_replication.evolution_executor import ExecutorRefusal, revalidate_member
    from services.research.alpha_replication.shared_stores import SharedReplicationGate

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "control-plane" / "persona"))
    pools = importlib.import_module("persona_strategy_pool")
    tenant, pid = "default", "p1"

    # Build a legacy close-fill Registry entry for the canonical momentum artifact.
    legacy_entry = genuine_entry(tenant=tenant)
    legacy_entry["version"] = "1.0.0"
    del legacy_entry["evaluation_summary"]["fill_convention"]
    del legacy_entry["replication_proof"]["evaluation_summary"]["fill_convention"]
    legacy_id = legacy_entry["registry_id"]
    legacy_snapshot = json.dumps(legacy_entry, sort_keys=True)

    # --- AC 1: Reproduce with the real code ---
    baseline = resolve_member_baseline(
        tenant_id=tenant,
        persona_id=pid,
        artifact_ref=legacy_id,
        checksum=legacy_entry["checksum"],
        entry=legacy_entry,
    )
    assert baseline["status"] == "no_approved_baseline"
    assert baseline["reason"] == "baseline_computed_under_decision_close_fill"
    assert baseline["operator_decision_required"] is True
    assert baseline["recorded_fill_convention"] == LEGACY_FILL_CONVENTION
    assert baseline["required_fill_convention"] == FILL_CONVENTION

    with pytest.raises(ExecutorRefusal, match="no_approved_member_baseline"):
        revalidate_member(
            artifact=legacy_entry["metadata"]["strategy_artifact"],
            dataset=recorded_dataset(),
            member={"joined_at": "2024-09-30T00:00:00Z"},
            baseline=baseline,
        )

    cand_entry = genuine_entry(tenant=tenant)
    cand_entry["registry_id"] = "cand-next-open-1"
    entries = {legacy_id: legacy_entry, "cand-next-open-1": cand_entry}
    store = pools.PersonaStrategyPoolStore(
        AtomicJsonRecordStore(tmp_path / "pool.json"),
        registry_reader=lambda ref, _: entries.get(ref, {}),
        now=lambda: "2026-11-16T00:00:00Z",
    )
    store.append(
        pid,
        tenant,
        pools.AppendPoolVersionRequest(
            expected_version=0,
            members=[
                pools.PoolMember(
                    artifact_ref=legacy_id,
                    checksum=legacy_entry["checksum"],
                    stage="active",
                    risk_weight=0.7,
                    baseline_ref=legacy_entry["approval_decision_id"],
                    joined_at="2024-08-01T00:00:00Z",
                )
            ],
            change_reason="seed legacy close-fill member",
        ),
        actor_id="test-owner",
    )
    refused_admit = store.decide(
        pid,
        tenant,
        pools.PoolDecisionRequest(
            expected_version=1,
            execution_context="paper",
            action="admit",
            candidate=pools.PoolCandidate(
                artifact_ref="cand-next-open-1",
                checksum=cand_entry["checksum"],
            ),
            books={},
            observations=idle_pool_observations(
                ["2024-10-01"] * 20, source_ref="test", gross_limit=100, drawdown_limit=0.1
            ),
            dataset=recorded_dataset(),
        ),
        actor_id="test-owner",
    )
    assert refused_admit["decision"]["status"] == "refused"
    assert refused_admit["decision"]["reasons"] == [
        f"{legacy_id}: baseline_computed_under_decision_close_fill"
    ]

    # --- AC 2, AC 4, AC 6, AC 7, AC 8: Real 2-year TWSE exchange re-qualification ---
    persona = {"persona_id": pid, "tenant_id": tenant, "lifecycle_state": "paper_owner", "mandate": "TW equity"}
    published = []
    memory_entries = []
    dataset_2yr = _recorded_2yr_exchange_dataset()
    env = {
        **__import__("services.evolution.test_auto_consumer_dispatch", fromlist=["ENV"]).ENV,
        "AUTO_CONSUMER_TELEMETRY_URL": "http://tel",
        "AUTO_CONSUMER_TELEMETRY_TOKEN": "telemetry-test",
        "AUTO_CONSUMER_RESEARCH_URL": "http://research",
        "AUTO_CONSUMER_RESEARCH_TOKEN": "research-test",
        "AUTO_CONSUMER_BFF_URL": "http://bff",
        "AUTO_CONSUMER_BFF_TOKEN": "operator-test",
        "AUTO_CONSUMER_MEMORY_URL": "http://mem",
        "AUTO_CONSUMER_MEMORY_TOKEN": "mem-test",
        "AUTO_CONSUMER_PROPOSER_TOKEN": "tok-evo-proposer",
        "AUTO_CONSUMER_PROPOSER_ID": "evolution-execute-proposer",
        "AUTO_CONSUMER_EVIDENCE_PATH": str(tmp_path / "evidence.jsonl"),
        "AUTO_CONSUMER_DECAY_LEDGER_PATH": str(tmp_path / "decay.jsonl"),
    }
    original_transport = dispatch_world["transport"]

    def transport(method, url, headers, body):
        if url == "http://per/api/personas?lifecycle_state=paper_owner":
            return 200, [persona]
        if url == "http://per/api/personas/p1":
            return 200, persona
        if url == "http://per/api/personas/p1/strategy-pool":
            return 200, store.current(pid, tenant)
        if url.endswith("/strategy-pool/decisions"):
            return 201, store.decide(pid, tenant, pools.PoolDecisionRequest.model_validate(body), actor_id="pool-owner")
        if url.endswith("/strategy-pool/versions"):
            return 201, store.append(pid, tenant, pools.AppendPoolVersionRequest.model_validate(body), actor_id="pool-owner")
        if url.startswith("http://bff/"):
            published.append(body["pool_version"])
            return 200, {"data": {"status": "published", "pool_version": body["pool_version"]}}
        if url == "http://mem/api/memory/writebacks/learn-feedback":
            entry_row = {"memory_id": f"mem-{len(memory_entries)+1}", "tags": list(body.get("tags") or []), "content": dict(body)}
            memory_entries.append(entry_row)
            return 201, {"status": "written", "memory_id": entry_row["memory_id"]}
        if url.startswith("http://reg/api/registry/strategy-artifacts/"):
            art_id = url.split("/strategy-artifacts/")[1].split("/")[0]
            return (200, {"entry": entries[art_id]}) if art_id in entries else (404, {})
        return original_transport(method, url, headers, body)

    def research_call(**kw):
        url, method = kw["url"], kw["method"]
        if method == "GET" and url.endswith("/strategy-artifacts/" + legacy_id):
            return {"entry": entries[legacy_id]}
        if method == "GET" and "/strategies/" in url and url.endswith("/strategy-artifacts"):
            return [{"entry": entries[legacy_id]}]
        if method == "GET" and "/strategy-specs/" in url:
            raise research._OwnerRejection(404, "not found")
        raise AssertionError(f"unexpected research owner call: {method} {url}")

    from services.research.alpha_replication import shared_stores

    monkeypatch.setattr(shared_stores, "SharedReplicationGate", SharedReplicationGate)
    monkeypatch.setattr(research, "_retrain_call", research_call)
    monkeypatch.setattr(research, "_pool_owner", lambda _: runtime.PoolOwner(env, transport))
    monkeypatch.setattr(research, "_RETRAIN_DATASET_LOADER", lambda *_: dataset_2yr)

    research_client = TestClient(research.app)

    def http_json(method, url, *, payload=None, timeout=20.0):
        resp = research_client.request(method, url.split("http://research", 1)[1], json=payload)
        return resp.status_code, resp.json()

    evolution_main.receipt_registry["research"] = ResearchPlaneAdapter(api_url="http://research", http_json=http_json)

    tick = runtime.run_consumer_tick(env, transport)
    assert not tick["errors"] and not tick["schedule"]["errors"], tick
    assert len(tick["approved"]) == 1
    decision_id = tick["approved"][0]
    decision = evolution_client.get("/api/evolution/proposals/" + decision_id).json()
    assert decision["action_type"] == "revalidate"
    assert decision["metadata"]["pool_member"]["mode"] == "requalify_legacy_baseline"

    _poll(dispatch_world)
    runs = research.get_store().list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "completed", run
    assert run["outcome"] == "revalidation_failed"
    assert run["outcome_reason"] == "requalification_failed_next_open_fill"
    assert run["operator_decision_required"] is False

    res = run["evolution_result"]
    assert res["passed"] is False
    assert res["fill_convention"] == FILL_CONVENTION
    assert res["dataset_id"] == "recorded:twse:template-demand:202401-202610"
    assert len(res["source_dataset_refs"]) == 68
    assert res["window"]["start_date"] == "2024-01-02"
    assert res["window"]["end_date"] == "2026-10-30"
    assert res["window"]["calendar_days"] == 1032
    assert res["window"]["trading_days"] == 683
    assert res["metrics"]["out_of_sample_sharpe_ratio"] < 0
    assert res["metrics"]["out_of_sample_max_drawdown"] > 0.25
    assert res["metrics"]["out_of_sample_round_trips"] == 106
    assert set(res["failed_checks"]) >= {
        "oos_sharpe_positive_after_costs",
        "oos_sharpe_exceeds_deflated_threshold",
        "walk_forward_consistent_across_folds",
        "oos_max_drawdown_within_mandate",
        "oos_sharpe_beats_buy_and_hold",
    }

    # Member is removed with reason requalification_failed_next_open_fill and verdict reference; stays in history.
    cur_pool = store.current(pid, tenant)
    assert cur_pool["pool_version"] == 3
    removed_member = cur_pool["members"][0]
    assert removed_member["artifact_ref"] == legacy_id
    assert removed_member["stage"] == "removed"
    assert removed_member["risk_weight"] == 0
    assert removed_member["removal_reason"] == "requalification_failed_next_open_fill"
    assert removed_member["removal_verdict_ref"] == res["verdict_ref"]
    assert cur_pool["decision"]["verdict_ref"] == res["verdict_ref"]
    assert len(store.versions(pid, tenant)) == 3
    assert persona["lifecycle_state"] == "paper_owner"

    # Outcome is learned in persona memory and excludes the family on the next demand.
    assert len(memory_entries) == 1
    assert "tried_family:strat-tw-replication-001" in memory_entries[0]["tags"]
    opened_demands = []

    def cycle_transport(method, service, path, t, body=None):
        if service == "memory" and method == "GET":
            return {"entries": memory_entries}
        if service == "research" and path == "/api/research/persona-demands/reconcile":
            return {"demands": []}
        if service == "research" and path == "/api/research/persona-demands" and method == "POST":
            opened_demands.append(body)
            return {"demand_id": "dem-after-fail-1", "created": True, "status": "open", **body}
        if service == "research" and path == "/api/research-orchestrator/alpha-replication/entries":
            return []
        if service == "registry":
            return {"entry": legacy_entry}
        raise AssertionError(f"unexpected cycle call: {method} {service} {path}")

    ports = HttpPoolCyclePorts(transport=cycle_transport, pool_store=store)
    cycle_out = PoolAutonomyCycle(ports, now=lambda: "2026-11-16T12:00:00Z").run(
        persona, {"pool": cur_pool, "pool_health": {}}, "pkt-after-fail"
    )
    assert cycle_out["status"] == "completed"
    assert len(opened_demands) == 1
    assert "strat-tw-replication-001" in opened_demands[0]["tried_families"]

    # Idempotent on next cadence tick; legacy entry and resolve_member_baseline are untouched (AC 6, AC 7).
    tick2 = runtime.run_consumer_tick(env, transport)
    assert tick2["approved"] == [] and tick2["schedule"]["proposed"] == []
    assert json.dumps(legacy_entry, sort_keys=True) == legacy_snapshot
    assert resolve_member_baseline(
        tenant_id=tenant,
        persona_id=pid,
        artifact_ref=legacy_id,
        checksum=legacy_entry["checksum"],
        entry=legacy_entry,
    )["reason"] == "baseline_computed_under_decision_close_fill"


def test_legacy_close_fill_member_defers_on_unusable_data_or_owner_error_then_replaces_passing_artifact_on_next_cadence(
    dispatch_world, research, monkeypatch, tmp_path,
):
    import json
    import services.evolution.auto_consumer_runtime as runtime
    from services.evolution import main as evolution_main
    from services.evolution.approved_baselines import resolve_member_baseline
    from services.evolution.dispatch_receipts import ResearchPlaneAdapter
    from services.evolution.test_approved_baselines import genuine_entry
    from services.evolution.test_auto_consumer_dispatch import _poll
    from services.execution.lean_runtime.member_virtual_book import FILL_CONVENTION
    from services.foundation.reliable_delivery import AtomicJsonRecordStore
    from services.registry.promotion.gate import PromotionGate
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.alpha_replication.shared_stores import SharedReplicationGate
    from services.research.tests.test_candidate_registration import RealGovernanceApprovalHarness
    from services.research.vectorbt.adapter.vectorbt_adapter import build_governed_tw_replication_dataset

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "control-plane" / "persona"))
    pools = importlib.import_module("persona_strategy_pool")
    tenant, pid = "default", "p1"
    dataset_2yr = _recorded_2yr_exchange_dataset()
    passing_art = _passing_2yr_klen_artifact(dataset_2yr)
    legacy_id = "reg-legacy-klen-close-001"
    passing_art["artifact_id"] = legacy_id
    passing_art["version"] = "1.0.0"
    legacy_checksum = strategy_artifact_checksum(passing_art)

    # Build a legacy close-fill Registry entry carrying the passing_art structure.
    legacy_entry = genuine_entry(tenant=tenant)
    legacy_entry["registry_id"] = legacy_id
    legacy_entry["version"] = "1.0.0"
    legacy_entry["checksum"] = legacy_checksum
    legacy_entry["metadata"] = {"strategy_artifact": passing_art, "persona_id": pid}
    legacy_entry["replication_proof"]["owner_verified_binding"]["artifact_checksum"] = legacy_checksum
    del legacy_entry["evaluation_summary"]["fill_convention"]
    del legacy_entry["replication_proof"]["evaluation_summary"]["fill_convention"]
    legacy_snapshot = json.dumps(legacy_entry, sort_keys=True)

    assert (
        resolve_member_baseline(
            tenant_id=tenant,
            persona_id=pid,
            artifact_ref=legacy_id,
            checksum=legacy_checksum,
            entry=legacy_entry,
        )["reason"]
        == "baseline_computed_under_decision_close_fill"
    )

    entries = {legacy_id: legacy_entry}
    store = pools.PersonaStrategyPoolStore(
        AtomicJsonRecordStore(tmp_path / "pool_pass.json"),
        registry_reader=lambda ref, _: entries.get(ref, {}),
        now=lambda: "2026-11-16T00:00:00Z",
        # POOL-ADMISSION-GOVERNED-UNIVERSE-20261125: paper admission reads the governed boundary (MarketBoundary row shape)
        market_boundary_provider=lambda: __import__("types").SimpleNamespace(
            snapshot_id="gmd-2026-11-15-0123456789abcdef", as_of="2026-11-16T00:00:00+00:00",
            symbols=("2330.TW", "2317.TW", "0050.TW")),
    )
    store.append(
        pid,
        tenant,
        pools.AppendPoolVersionRequest(
            expected_version=0,
            members=[
                pools.PoolMember(
                    artifact_ref=legacy_id,
                    checksum=legacy_checksum,
                    stage="active",
                    risk_weight=0.7,
                    baseline_ref=legacy_entry["approval_decision_id"],
                    joined_at="2024-08-01T00:00:00Z",
                )
            ],
            change_reason="seed legacy close-fill klen member",
        ),
        actor_id="test-owner",
    )

    persona = {"persona_id": pid, "tenant_id": tenant, "lifecycle_state": "paper_owner"}
    published = []
    gov_harness = RealGovernanceApprovalHarness()
    env = {
        **__import__("services.evolution.test_auto_consumer_dispatch", fromlist=["ENV"]).ENV,
        "AUTO_CONSUMER_TELEMETRY_URL": "http://tel",
        "AUTO_CONSUMER_TELEMETRY_TOKEN": "telemetry-test",
        "AUTO_CONSUMER_RESEARCH_URL": "http://research",
        "AUTO_CONSUMER_RESEARCH_TOKEN": "research-test",
        "AUTO_CONSUMER_BFF_URL": "http://bff",
        "AUTO_CONSUMER_BFF_TOKEN": "operator-test",
        "AUTO_CONSUMER_PROPOSER_TOKEN": "tok-evo-proposer",
        "AUTO_CONSUMER_PROPOSER_ID": "evolution-execute-proposer",
        "AUTO_CONSUMER_EVIDENCE_PATH": str(tmp_path / "evidence_pass.jsonl"),
        "AUTO_CONSUMER_DECAY_LEDGER_PATH": str(tmp_path / "decay_pass.jsonl"),
    }
    original_transport = dispatch_world["transport"]

    def transport(method, url, headers, body):
        if url == "http://per/api/personas?lifecycle_state=paper_owner":
            return 200, [persona]
        if url == "http://per/api/personas/p1":
            return 200, persona
        if url == "http://per/api/personas/p1/strategy-pool":
            return 200, store.current(pid, tenant)
        if url.endswith("/strategy-pool/decisions"):
            return 201, store.decide(pid, tenant, pools.PoolDecisionRequest.model_validate(body), actor_id="pool-owner")
        if url.endswith("/strategy-pool/versions"):
            return 201, store.append(pid, tenant, pools.AppendPoolVersionRequest.model_validate(body), actor_id="pool-owner")
        if url.startswith("http://bff/"):
            published.append(body["pool_version"])
            return 200, {"data": {"status": "published", "pool_version": body["pool_version"]}}
        if url.startswith("http://reg/api/registry/strategy-artifacts"):
            if method == "POST" and url.endswith("/api/registry/strategy-artifacts"):
                art = body["strategy_artifact"]
                cs = strategy_artifact_checksum(art)
                verified = PromotionGate.verify_admitted_replication_proof(
                    body["replication_proof"],
                    body["evaluation_summary"],
                    art["lineage"],
                    backtest_evaluation=body["backtest_evaluation"],
                    artifact_checksum=cs,
                )
                verified["owner_verified_binding"]["producer_verified"] = True
                entries[body["registry_id"]] = {
                    "registry_id": body["registry_id"],
                    "artifact_type": "execution_bundle",
                    "version": art["version"],
                    "artifact_state": "candidate",
                    "checksum": cs,
                    "owner_tenant": tenant,
                    "evaluation_summary": body["evaluation_summary"],
                    "lineage": art["lineage"],
                    "producer_run_id": body["producer_run_id"],
                    "replication_success": True,
                    "replication_proof": verified,
                    "metadata": {**dict(body.get("metadata") or {}), "strategy_artifact": art, "persona_id": pid},
                }
                return 201, {"entry": entries[body["registry_id"]]}
            art_id = url.split("/strategy-artifacts/")[1].split("/")[0]
            if url.endswith("/advance"):
                entries[art_id].update(
                    artifact_state="approved",
                    approval_decision_id=body["approval_decision_id"],
                )
                return 200, {"entry": entries[art_id]}
            return (200, {"entry": entries[art_id]}) if art_id in entries else (404, {})
        if url.startswith("http://gov/api/governance/approvals"):
            path = url[len("http://gov"):]
            if path.startswith("/api/governance/approvals/apv-tpl-") or (
                method == "POST" and path == "/api/governance/approvals" and str((body or {}).get("decision_id", "")).startswith("apv-tpl-")
            ):
                try:
                    return 200, gov_harness.handle(method, path, tenant, body)
                except LookupError:
                    return 404, {"detail": "not found"}
        return original_transport(method, url, headers, body)

    state = {"dataset": build_governed_tw_replication_dataset(), "spec_down": False}

    def research_call(**kw):
        url, method = kw["url"], kw["method"]
        if method == "GET" and url.endswith("/strategy-artifacts/" + legacy_id):
            return {"entry": entries[legacy_id]}
        if method == "GET" and "/strategies/" in url and url.endswith("/strategy-artifacts"):
            return [{"entry": e} for e in entries.values()]
        if method == "GET" and "/strategy-specs/" in url:
            if state["spec_down"]:
                raise research._OwnerRejection(503, "registry strategy_spec read unavailable")
            raise research._OwnerRejection(404, "not found")
        raise AssertionError(f"unexpected research call: {method} {url}")

    from services.research.alpha_replication import shared_stores

    monkeypatch.setattr(shared_stores, "SharedReplicationGate", SharedReplicationGate)
    monkeypatch.setattr(research, "_retrain_call", research_call)
    monkeypatch.setattr(research, "_pool_owner", lambda _: runtime.PoolOwner(env, transport))
    monkeypatch.setattr(research, "_RETRAIN_DATASET_LOADER", lambda *_: state["dataset"])

    research_client = TestClient(research.app)

    def http_json(method, url, *, payload=None, timeout=20.0):
        resp = research_client.request(method, url.split("http://research", 1)[1], json=payload)
        return resp.status_code, resp.json()

    evolution_main.receipt_registry["research"] = ResearchPlaneAdapter(api_url="http://research", http_json=http_json)

    # 1. Synthetic dataset is refused as input -> operator_decision_required, member NOT removed (AC 2, AC 5).
    runtime.run_consumer_tick(env, transport)
    _poll(dispatch_world)
    run = research.get_store().list_runs()[0]
    assert run["status"] == "queued"
    assert run["outcome"] == "operator_decision_required"
    assert run["outcome_reason"] == "synthetic_or_fixture_dataset_refused"
    assert run["operator_decision_required"] is True
    assert store.current(pid, tenant)["pool_version"] == 1
    assert store.current(pid, tenant)["members"][0]["stage"] == "active"

    # 2. Short (< 2 years) dataset on next cadence -> insufficient_point_in_time_history, member NOT removed (AC 5).
    state["dataset"] = recorded_dataset()
    runtime.run_consumer_tick(env, transport)
    _poll(dispatch_world)
    run = research.get_store().list_runs()[0]
    assert run["attempt_number"] == 2
    assert run["status"] == "queued"
    assert run["outcome"] == "operator_decision_required"
    assert run["outcome_reason"] == "insufficient_point_in_time_history"
    assert store.current(pid, tenant)["pool_version"] == 1
    assert store.current(pid, tenant)["members"][0]["stage"] == "active"

    # 3. Owner read failure on next cadence -> owner_read_failed:strategy_spec, member NOT removed (AC 5).
    state["dataset"] = dataset_2yr
    state["spec_down"] = True
    runtime.run_consumer_tick(env, transport)
    _poll(dispatch_world)
    run = research.get_store().list_runs()[0]
    assert run["attempt_number"] == 3
    assert run["status"] == "queued"
    assert run["outcome"] == "operator_decision_required"
    assert run["outcome_reason"] == "owner_read_failed:strategy_spec"
    assert store.current(pid, tenant)["pool_version"] == 1
    assert store.current(pid, tenant)["members"][0]["stage"] == "active"

    # 4. Next cadence with governed 2-year TWSE exchange dataset succeeds and replaces legacy member in one pool version (AC 3, AC 8).
    state["spec_down"] = False
    runtime.run_consumer_tick(env, transport)
    _poll(dispatch_world)
    run = research.get_store().list_runs()[0]
    assert run["attempt_number"] == 4
    assert run["status"] == "completed", run
    assert run["outcome"] == "revalidation_passed"
    assert run["outcome_reason"] == "replaced_by_requalified_entry"
    assert run["operator_decision_required"] is False

    res = run["evolution_result"]
    assert res["passed"] is True
    assert res["fill_convention"] == FILL_CONVENTION
    assert res["dataset_id"] == "recorded:twse:template-demand:202401-202610"
    assert res["metrics"]["out_of_sample_sharpe_ratio"] > res["metrics"]["tw_0050_sharpe_ratio"]
    assert res["metrics"]["out_of_sample_max_drawdown"] <= 0.25
    assert res["metrics"]["out_of_sample_round_trips"] >= 20
    assert res["failed_checks"] == []

    new_id = run["candidate_artifact_id"]
    assert new_id != legacy_id and new_id in entries
    new_entry = entries[new_id]
    assert new_entry["artifact_state"] == "approved"
    assert new_entry["lineage"]["parent_registry_ids"] == [legacy_id]
    new_baseline = resolve_member_baseline(
        tenant_id=tenant,
        persona_id=pid,
        artifact_ref=new_id,
        checksum=new_entry["checksum"],
        entry=new_entry,
    )
    assert new_baseline["status"] == "approved"
    assert new_baseline["fill_convention"] == FILL_CONVENTION

    # One pool version (pool_version 1 -> 2) removed legacy_id and admitted new_id under all criteria.
    final_pool = store.current(pid, tenant)
    assert final_pool["pool_version"] == 2
    assert final_pool["decision"]["status"] == "applied"
    assert final_pool["decision"]["replaced_artifact_ref"] == legacy_id
    assert final_pool["decision"]["backtest"]["admitted"] is True
    assert final_pool["decision"]["backtest"]["pool_idle"] is True
    by_ref = {m["artifact_ref"]: m for m in final_pool["members"]}
    assert by_ref[legacy_id]["stage"] == "removed"
    assert by_ref[legacy_id]["removal_reason"] == "replaced_by_requalified_entry"
    assert by_ref[legacy_id]["risk_weight"] == 0
    assert by_ref[new_id]["stage"] == "trial"
    assert by_ref[new_id]["parent_ref"] == legacy_id
    assert by_ref[new_id]["risk_weight"] > 0
    assert persona["lifecycle_state"] == "paper_owner"

    # Legacy Registry entry is completely untouched and still resolves to no_approved_baseline (AC 7).
    assert json.dumps(legacy_entry, sort_keys=True) == legacy_snapshot
    assert (
        resolve_member_baseline(
            tenant_id=tenant,
            persona_id=pid,
            artifact_ref=legacy_id,
            checksum=legacy_checksum,
            entry=legacy_entry,
        )["reason"]
        == "baseline_computed_under_decision_close_fill"
    )

