"""Isolated PostgreSQL + mounted HTTP proof, including process restart/CAS.

Run with GOV_APPROVAL_TEST_DSN pointing only to a dedicated gov_approval_test
 database. JWT signing material is generated in memory for synthetic principals.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import psycopg
import pytest


def token(secret, **overrides):
    payload = dict(sub='synthetic-reviewer', tenant_id='synthetic-tenant',
                   roles=['governance_reviewer'], iss='isolated-governance-test',
                   aud='isolated-governance', exp=time.time() + 900)
    payload.update(overrides)
    payload = {key: value for key, value in payload.items() if value is not None}
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=')
    signed = encode(b'{"alg":"HS256","typ":"JWT"}') + b'.' + encode(json.dumps(payload).encode())
    return (signed + b'.' + encode(hmac.new(secret.encode(), signed, hashlib.sha256).digest())).decode()


@pytest.fixture(scope='module')
def owner_env():
    dsn = os.environ.get('GOV_APPROVAL_TEST_DSN', '')
    # Fail, never skip or silently select a shared business database.
    assert dsn and psycopg.conninfo.conninfo_to_dict(dsn).get('dbname') == 'gov_approval_test'
    schema = 'gov_test_' + uuid.uuid4().hex
    env = dict(os.environ)
    secret = secrets.token_hex(32)
    env.update(GOVERNANCE_STORE_BACKEND='postgres', GOVERNANCE_STORE_DSN=dsn,
               GOVERNANCE_AUDIT_BACKEND='postgres', GOVERNANCE_AUDIT_DSN=dsn,
               PANTHEON_GOVERNANCE_JWT_SECRET=secret,
               PANTHEON_GOVERNANCE_JWT_ISSUER='isolated-governance-test',
               PANTHEON_GOVERNANCE_JWT_AUDIENCE='isolated-governance',
               GOVERNANCE_DATA_DIR=tempfile.mkdtemp(prefix='gov-approval-test-'))
    for variable, table in [('GOVERNANCE_STORE_TABLE','decisions'), ('GOVERNANCE_AUDIT_TABLE','audit'),
                            ('GOVERNANCE_FREEZE_ORDER_STORE_TABLE','freeze'), ('GOVERNANCE_ROLLBACK_STORE_TABLE','rollback'),
                            ('GOVERNANCE_HUMAN_GATE_STORE_TABLE','human_gate'), ('GOVERNANCE_CONSULTATION_HANDOFF_STORE_TABLE','consultation')]:
        env[variable] = schema + '.' + table
    yield env


@contextmanager
def server(env):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # Capture bounded foreground child output; always wait and collect exit.
    with tempfile.TemporaryFile(mode='w+') as output:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'services.governance.main:app',
                                    '--host', '127.0.0.1', '--port', str(port), '--log-level', 'warning'],
                                   env=env, stdout=output, stderr=subprocess.STDOUT)
        url = f'http://127.0.0.1:{port}'
        try:
            for _ in range(150):
                if process.poll() is not None:
                    output.seek(0)
                    pytest.fail('Governance failed startup: ' + output.read())
                try:
                    if httpx.get(url+'/health', timeout=.5).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail('Governance startup timed out')
            yield url
        finally:
            process.terminate()
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                pytest.fail('Governance failed to terminate')
            output.seek(0)
            terminal_output = output.read()
            assert code in (0, -15), terminal_output


@pytest.fixture(scope='module')
def mounted(owner_env):
    with server(owner_env) as url:
        yield url


def headers(env, key=None, **claims):
    result = {'Authorization': 'Bearer ' + token(env['PANTHEON_GOVERNANCE_JWT_SECRET'], **claims)}
    if key:
        result['Idempotency-Key'] = key
    return result


def proposal(**overrides):
    result = dict(target_type='registry_entry', target_id='synthetic-artifact', target_version='1.0.0',
                  tenant_id='synthetic-tenant', owner_user_id='synthetic-reviewer', expected_version=0,
                  candidate_digest='a'*64, expires_at='2099-01-01T00:00:00Z')
    result.update(overrides)
    return result


def post(url, path, env, body, key=None, **claims):
    return httpx.post(url+'/api/governance/approvals'+path, json=body,
                      headers=headers(env, key or uuid.uuid4().hex, **claims), timeout=10)


def approved(url, env, risk='low', role='governance_reviewer', **fields):
    r = post(url, '', env, proposal(risk_level=risk, **fields), roles=[role])
    assert r.status_code == 201, r.text
    decision_id = r.json()['decision_id']
    r = post(url, f'/{decision_id}/review', env, dict(expected_version=1, actor_id='synthetic-reviewer', actor_role=role), roles=[role])
    assert r.status_code == 200, r.text
    r = post(url, f'/{decision_id}/decide', env, dict(expected_version=2, actor_id='synthetic-reviewer', actor_role=role,
                                                   outcome='approved', rationale='isolated proof'), roles=[role])
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize('risk,role', [('low','governance_reviewer'),('low','automated_gate'),
    ('medium','governance_reviewer'),('medium','risk_owner'),('high','risk_owner'),
    ('high','governance_committee'),('critical','governance_committee')])
def test_all_risk_positive(mounted, owner_env, risk, role):
    decision = approved(mounted, owner_env, risk, role)
    response = httpx.get(mounted+'/api/governance/approvals/'+decision['decision_id'], headers=headers(owner_env))
    assert response.status_code == 200
    assert response.json() == decision
    assert decision['version'] == 3 and decision['event_id']


@pytest.mark.parametrize('claims', [{'sub': None},{'tenant_id':None},{'roles':None},{'roles':[]},
    {'exp':None},{'exp':0},{'exp':'invalid'},{'exp':float('inf')},{'iss':'wrong'},{'aud':'wrong'}])
def test_invalid_claims_fail_closed(mounted, owner_env, claims):
    r = post(mounted, '', owner_env, proposal(), **claims)
    assert r.status_code in (401,403), r.text


def test_private_ids_and_body_escalation(mounted, owner_env):
    decision = approved(mounted, owner_env)
    private = decision['decision_id']
    for path in ['', '/latest-approved?target_type=registry_entry&target_id=synthetic-artifact', '/'+private]:
        r = httpx.get(mounted+'/api/governance/approvals'+path, headers=headers(owner_env, tenant_id='other'))
        assert r.status_code == (404 if path == '/'+private else 200)
        assert private not in r.text
    r = post(mounted, f'/{private}/revoke', owner_env,
             dict(expected_version=3,actor_id='synthetic-reviewer',actor_role='risk_owner'))
    assert r.status_code == 403
    r = post(mounted, f'/{private}/revoke', owner_env,
             dict(expected_version=3,actor_id='synthetic-reviewer',actor_role='risk_owner'), roles=['risk_owner'], tenant_id='other')
    assert r.status_code == 404


def test_two_process_cas_and_original_replay(mounted, owner_env):
    key = uuid.uuid4().hex
    body = proposal()
    first = post(mounted, '', owner_env, body, key)
    assert first.status_code == 201, first.text
    decision = first.json()
    with server(owner_env) as second:
        # Fresh interpreter and DB-backed original receipt after process boundary.
        retry = post(second, '', owner_env, body, key)
        assert retry.status_code == 201 and retry.json() == decision
        conflict = post(second, '', owner_env, proposal(target_version='2.0.0'), key)
        assert conflict.status_code == 409
        path = '/'+decision['decision_id']+'/review'
        review = dict(expected_version=1,actor_id='synthetic-reviewer',actor_role='governance_reviewer')
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(post, url, path, owner_env, review) for url in (mounted,second)]
            results = [f.result() for f in futures]
        assert sorted(r.status_code for r in results) == [200,409]
        assert post(second, '', owner_env, body, key).json() == decision


def test_atomic_rollback_on_audit_failure(mounted, owner_env):
    dsn = owner_env['GOVERNANCE_STORE_DSN']
    table = owner_env['GOVERNANCE_AUDIT_TABLE']
    from psycopg import sql
    with psycopg.connect(dsn) as conn:
        conn.execute(sql.SQL('ALTER TABLE {} ADD CONSTRAINT reject_test CHECK (false) NOT VALID').format(sql.Identifier(*table.split('.'))))
    key = uuid.uuid4().hex
    body = proposal(decision_id='failure-'+key)
    try:
        r = post(mounted, '', owner_env, body, key)
        assert r.status_code == 503
        r = httpx.get(mounted+'/api/governance/approvals/'+body['decision_id'], headers=headers(owner_env))
        assert r.status_code == 404
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL('ALTER TABLE {} DROP CONSTRAINT reject_test').format(sql.Identifier(*table.split('.'))))
    assert post(mounted, '', owner_env, body, key).status_code == 201
