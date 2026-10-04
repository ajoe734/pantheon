"""Real isolated PostgreSQL proof; run with RANKING_TEST_DSN, never a hosted DSN."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff import test_bff_promotion_review_governance as g
from services.control_plane.bff.bootstrap.dependencies import AppDependencies
from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.core.errors import register_error_handlers
from services.rankings.store import RankingWriteStore, RankingReadStore, RankingConflictError
from services.rankings.snapshots import snapshot_record, admit_snapshot
from services.runtime_auth_inbound import encode_jwt_hs256

REAL_RANKING_OWNER = True  # Disable the legacy saved-evaluator autouse fixture.
QUARTER = '2026-Q1'
NOW = 1770000000.0


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv('RANKING_TEST_DSN')
    if not dsn:
        pytest.skip('RANKING_TEST_DSN must name a disposable local PostgreSQL instance')
    import psycopg
    assert urlparse(dsn).hostname in ('127.0.0.1', 'localhost')
    schema = 'ranking_test_' + uuid.uuid4().hex
    table = schema + '.rankings'
    writer = RankingWriteStore(dsn, table)
    monkeypatch.setenv('RANKING_STORE_DSN', dsn)
    monkeypatch.setenv('RANKING_STORE_TABLE', table)
    monkeypatch.setenv('RANKING_STORE_BOOTSTRAP', '0')
    try:
        yield dsn, table, writer
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def evaluator():
    spec = importlib.util.spec_from_file_location('snapshot_owner_evaluator', Path(__file__).resolve().parents[3] / 'persona-evaluator-agent/persona_evaluator_agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mounted(pg, tmp_path, monkeypatch):
    monkeypatch.setenv('BFF_DATA_DIR', str(tmp_path / 'bff'))
    monkeypatch.setenv('PANTHEON_BFF_TENANT_ID', g._PM12_ELIGIBLE_TENANT_ID)
    # A read-only DB transaction also rejects DDL, including CREATE IF NOT EXISTS.
    monkeypatch.setenv('RANKING_STORE_DSN', pg[0] + '?options=-cdefault_transaction_read_only=on')
    projection = g.PromotionReviewTestReadPorts(allow_fallback=True)
    with monkeypatch.context() as no_writer:
        no_writer.setattr(RankingWriteStore, '__init__', lambda *a, **kw: pytest.fail('BFF constructed a Rankings writer'))
        deps = AppDependencies.create_default(read_surface=projection)
    assert type(deps.ranking_write_owner._store) is RankingReadStore
    assert not hasattr(deps.ranking_write_owner, 'put_ranking_snapshot')
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(create_personas_router(service=PersonaService(
        read_store=deps.read_surface, command_store=deps.command_store,
        write_owner=deps.persona_write_owner, ranking_write_owner=deps.ranking_write_owner,
    )))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, projection, deps


def ranking(client):
    response = client.get('/bff/management/quarterly-ranking', headers=g.OPERATOR_HEADERS,
                          params={'quarter': QUARTER, 'page_size': 200})
    assert response.status_code == 200, response.text
    return response.json()


def test_mounted_get_owner_admission_restart_and_saved_readback(pg, mounted, evaluator, tmp_path, monkeypatch):
    client, projection, deps = mounted
    first = ranking(client)
    snapshot_id = first['data']['ranking_snapshot_id']
    for _ in range(3):
        assert ranking(client)['data']['ranking_snapshot_id'] == snapshot_id
    assert pg[2].get_ranking_snapshot(snapshot_id) is None
    assert pg[2]._records_table.list_all() == []
    asks, proposals = [], []

    def fetch(url, data=None, **kwargs):
        if 'quarterly-ranking?' in url:
            return ranking(client)
        if '/structured' in url:
            asks.append(data)
            inputs, _ = evaluator.collect_evidence('http://bff', QUARTER, {}, lambda *a, **k: first)
            item = inputs[0]
            return {'data': {'output': {'structured_data': {'recommendations': [{
                'persona_id': item['persona_id'], 'action_id': 'reduce_capital_access',
                'rationale': 'Saved advisory evidence.', 'evidence_ref_ids': item['evidence_ref_ids'],
            }]}}}}
        proposals.append(data)
        raise AssertionError('Advisory recommendation must not submit a proposal')

    state_path = tmp_path / 'evaluator.json'
    args = dict(store=evaluator.Store(state_path), bff_url='http://bff', bff_headers={},
                adapter_url='http://adapter', adapter_token='local-test', governance_url='http://governance',
                governance_token='local-test', tenant=g._PM12_ELIGIBLE_TENANT_ID, actor='evaluator',
                fetch=fetch, now=lambda: NOW, ranking_store=pg[2])
    out = evaluator.run_once(**args)
    assert out['status'] == 'ok', out
    stored = pg[2].get_ranking_snapshot(snapshot_id)
    assert stored is not None and stored.evidence_assertion_digests
    assert stored.items == snapshot_record(first['data']['items'], surface='quarterly', period=QUARTER).items
    assert len(pg[2]._records_table.list_all()) == 1
    # Fresh OS process reads the same actual row, not a parent-process cache.
    code = 'from services.rankings.store import RankingReadStore; import json,sys; print(json.dumps(RankingReadStore(sys.argv[1],sys.argv[2]).get_ranking_snapshot(sys.argv[3]).to_canonical_dict()))'
    fresh = subprocess.run([sys.executable, '-c', code, pg[0], pg[1], snapshot_id], capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(fresh.stdout) == stored.to_canonical_dict()
    repeat = evaluator.run_once(**{**args, 'store': evaluator.Store(state_path), 'ranking_store': RankingWriteStore(pg[0], pg[1], False), 'now': lambda: NOW + 60})
    assert repeat['reused'] and len(asks) == 1 and proposals == []
    assert pg[2].get_ranking_snapshot(snapshot_id).created_at == stored.created_at
    # Genuine evaluator HTTP readback after reopening its result file.
    server = evaluator.serve(evaluator.Store(state_path), 'local-read-token', 0)
    monkeypatch.setenv('PERSONA_EVALUATOR_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('PERSONA_EVALUATOR_READ_TOKEN', 'local-read-token')
    try:
        response = client.get('/bff/management/quarterly-ranking/recommendations', headers=g.OPERATOR_HEADERS, params={'quarter': QUARTER})
        assert response.status_code == 200, response.text
        rec = response.json()['data']['items'][0]
        assert rec['ranking_snapshot_id'] == snapshot_id and rec['rationale'] == 'Saved advisory evidence.'
        assert len(pg[2]._records_table.list_all()) == 1
        # Saved result exists but backing snapshot is unavailable: explicit failure.
        monkeypatch.setattr(deps.ranking_write_owner._store, 'get_ranking_snapshot', lambda _: None)
        missing = client.get('/bff/management/quarterly-ranking/recommendations', headers=g.OPERATOR_HEADERS, params={'quarter': QUARTER})
        assert missing.status_code == 503, missing.text
    finally:
        server.shutdown()
        server.server_close()


def test_actual_concurrent_replay_and_conflict(pg):
    dsn, table, store = pg
    record = snapshot_record([{'persona_id': 'p1', 'rank': 1, 'evidence_refs': [{'refId': 'e1'}]}], surface='quarterly', period=QUARTER)
    barrier = threading.Barrier(2)
    def write(i):
        owner = RankingWriteStore(dsn, table, False)
        barrier.wait(timeout=5)
        return admit_snapshot(owner, replace(record, created_at=f'2026-01-01T00:00:0{i}Z'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, (0, 1)))
    assert outcomes[0] == outcomes[1] == store.get_ranking_snapshot(record.ranking_snapshot_id)
    assert len(store._records_table.list_all()) == 1
    with pytest.raises(RankingConflictError):
        admit_snapshot(store, replace(record, items=[{'persona_id': 'conflict'}]))
    with pytest.raises(RankingConflictError):
        admit_snapshot(store, replace(record, evidence_assertion_digests={'p1': ['changed']}))


@pytest.mark.parametrize('claims', [{}, {'tenant_id': 'foreign', 'allowed_tenants': ['foreign']}])
def test_signed_unauthorized_tenant_cannot_produce_snapshot(pg, mounted, monkeypatch, claims):
    client, _, _ = mounted
    secret = 'isolated-ranking-jwt'
    for key, value in {'PANTHEON_BFF_AUTH_STUB': '', 'PANTHEON_BFF_AUTH_MODE': 'strict',
                       'PANTHEON_BFF_JWT_SECRET': secret, 'PANTHEON_BFF_JWT_ISSUER': 'test-ranking',
                       'PANTHEON_BFF_JWT_AUDIENCE': 'bff', 'PANTHEON_BFF_MFA_REQUIRED': 'false'}.items():
        monkeypatch.setenv(key, value)
    now = int(time.time())
    token = encode_jwt_hs256({'sub': 'caller', 'roles': ['operator'], **claims,
                              'iss': 'test-ranking', 'aud': 'bff', 'iat': now, 'exp': now + 60}, secret=secret)
    response = client.get('/bff/management/quarterly-ranking', headers={'Authorization': f'Bearer {token}'}, params={'quarter': QUARTER})
    assert response.status_code in (200, 403), response.text
    assert not (response.json().get('data') or {}).get('items')
    assert pg[2]._records_table.list_all() == []
