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
def server(env, app="services.governance.main:app"):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # Capture bounded foreground child output; always wait and collect exit.
    with tempfile.TemporaryFile(mode='w+') as output:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', app,
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


@contextmanager
def approved_registry_owners(env, *, strategy_id='strategy-l12-dep',
                             capital_pool_id='pool-l12-dep', persona_id='persona-l12-dep',
                             execution_bundle=False):
    """Real scoped Registry/Governance HTTP owners, on dedicated PG schemas.

    Consumers receive owner URLs and read principals only. No caller-provided
    approval object or local JSON snapshot authorizes the consumer.
    """
    registry_env = dict(env)
    schema = 'registry_gov_test_' + uuid.uuid4().hex
    registry_env.update(
        REGISTRY_STORE_BACKEND='postgres', REGISTRY_STORE_DSN=env['GOVERNANCE_STORE_DSN'],
        REGISTRY_ENTRIES_TABLE=schema+'.entries', REGISTRY_RECEIPTS_TABLE=schema+'.receipts',
        PANTHEON_REGISTRY_AUTH_MODE='strict',
        PANTHEON_REGISTRY_JWT_SECRET=env['PANTHEON_GOVERNANCE_JWT_SECRET'],
        PANTHEON_REGISTRY_JWT_ISSUER='isolated-governance-test',
        PANTHEON_REGISTRY_JWT_AUDIENCE='isolated-registry')
    read_token = token(env['PANTHEON_GOVERNANCE_JWT_SECRET'], roles=['approval_reader'])
    registry_token = token(env['PANTHEON_GOVERNANCE_JWT_SECRET'], roles=['operator'],
                           tenant='synthetic-tenant', aud='isolated-registry')
    with server(env) as governance_url:
        registry_env.update(REGISTRY_GOVERNANCE_BASE_URL=governance_url,
                            REGISTRY_GOVERNANCE_SERVICE_TOKEN=read_token)
        with server(registry_env, 'services.registry.service:app') as registry_url:
            with httpx.Client(base_url=registry_url, headers={'Authorization': 'Bearer '+registry_token}, timeout=10) as client:
                if execution_bundle:
                    from services.registry.strategy_artifact import load_strategy_artifact_registration, BUILTIN_STRATEGY_ARTIFACT_PATHS
                    artifact = load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])['strategy_artifact']
                    artifact.update(artifact_id='isolated-bundle-'+uuid.uuid4().hex, strategy_id=strategy_id, version='1.0.0')
                    artifact['binding_intent'].update(persona_id=persona_id,
                                                      persona_capital_binding_id='pcb-l12-dep')
                    r = client.post('/api/registry/strategy-artifacts', json={'strategy_artifact': artifact})
                else:
                    r = client.post('/api/registry/entries', headers={'Idempotency-Key': uuid.uuid4().hex}, json={
                        'artifact_type': 'model_artifact', 'strategy_id': strategy_id,
                        'version': '1.0.0', 'artifact_state': 'draft', 'checksum': 'sha256:'+'a'*64,
                        'lineage': {'source_run_ids': ['isolated-governance-proof']},
                        'storage_ref': {'backend': 'object_store', 'path': 'isolated/model.bin'}})
                assert r.status_code == 200, r.text
                entry = r.json()['entry']
                registry_id = entry['registry_id']
                if entry['artifact_state'] == 'draft':
                    r = client.post(f'/api/registry/entries/{registry_id}/advance', json={
                        'target_state': 'candidate', 'expected_artifact_state': 'draft',
                        'expected_version': entry['version'], 'expected_updated_at': entry['updated_at'],
                        'command_key': uuid.uuid4().hex})
                    assert r.status_code == 200, r.text
                    candidate = r.json()['entry']
                else:
                    candidate = entry
                decision = approved(governance_url, env, target_id=registry_id,
                                    candidate_digest=entry['checksum'],
                                    capital_pool_id=capital_pool_id, persona_id=persona_id)
                approval_command = {
                    'target_state': 'approved', 'expected_artifact_state': 'candidate',
                    'expected_version': candidate['version'], 'expected_updated_at': candidate['updated_at'],
                    'command_key': uuid.uuid4().hex, 'approval_decision_id': decision['decision_id']}
                r = client.post(f'/api/registry/entries/{registry_id}/advance', json=approval_command)
                assert r.status_code == 200, r.text
                readback = client.get(f'/api/registry/entries/{registry_id}')
                assert readback.status_code == 200, readback.text
                assert readback.json() == r.json()
                yield dict(registry_url=registry_url, governance_url=governance_url,
                           registry_token=registry_token, governance_token=read_token,
                           entry=readback.json()['entry'], decision=decision, registry_env=registry_env,
                           approval_command=approval_command, approved_view=readback.json())


def test_deciding_competition_and_lost_response_replay(mounted, owner_env):
    proposed = post(mounted, '', owner_env, proposal()).json()
    path = '/'+proposed['decision_id']
    reviewed = post(mounted, path+'/review', owner_env, dict(expected_version=1, actor_id='synthetic-reviewer', actor_role='governance_reviewer'))
    assert reviewed.status_code == 200, reviewed.text
    commands = [dict(expected_version=2, actor_id=actor, actor_role='governance_reviewer', outcome=outcome, rationale='competing isolated decision')
                for actor, outcome in [('reviewer-one', 'approved'), ('reviewer-two', 'rejected')]]
    keys = [uuid.uuid4().hex, uuid.uuid4().hex]
    with server(owner_env) as second:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(post, url, path+'/decide', owner_env, body, key, sub=body['actor_id'])
                       for url, body, key in zip((mounted, second), commands, keys)]
            responses = [f.result() for f in futures]
        assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(i for i, response in enumerate(responses) if response.status_code == 200)
    original = responses[winner].json()
    with server(owner_env) as restarted:
        replay = post(restarted, path+'/decide', owner_env, commands[winner], keys[winner], sub=commands[winner]['actor_id'])
        assert replay.status_code == 200 and replay.json() == original
        divergent = post(restarted, path+'/decide', owner_env, {**commands[winner], 'rationale': 'changed'}, keys[winner], sub=commands[winner]['actor_id'])
        assert divergent.status_code == 409


@pytest.mark.parametrize('failure', ['receipt', 'commit'])
def test_receipt_and_commit_failure_roll_back_every_record(mounted, owner_env, failure):
    from psycopg import sql
    schema, decision_table = owner_env['GOVERNANCE_STORE_TABLE'].split('.')
    receipt_table = decision_table+'_receipts'
    audit_table = owner_env['GOVERNANCE_AUDIT_TABLE'].split('.')[1]
    key = uuid.uuid4().hex
    body = proposal(decision_id='failure-'+key)
    with psycopg.connect(owner_env['GOVERNANCE_STORE_DSN']) as conn:
        if failure == 'receipt':
            # Reservation succeeds, final immutable receipt write fails.
            conn.execute(sql.SQL("ALTER TABLE {} ADD CONSTRAINT reject_receipt CHECK (NOT (payload ? 'response')) NOT VALID").format(sql.Identifier(schema, receipt_table)))
        else:
            conn.execute(sql.SQL("CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'isolated deferred commit failure'; END $$").format(sql.Identifier(schema, 'fail_commit')))
            conn.execute(sql.SQL('CREATE CONSTRAINT TRIGGER fail_commit AFTER INSERT ON {} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {}()').format(sql.Identifier(schema, audit_table), sql.Identifier(schema, 'fail_commit')))
    try:
        response = post(mounted, '', owner_env, body, key)
        assert response.status_code == 503, response.text
        readback = httpx.get(mounted+'/api/governance/approvals/'+body['decision_id'], headers=headers(owner_env))
        assert readback.status_code == 404
        with psycopg.connect(owner_env['GOVERNANCE_STORE_DSN']) as conn:
            for table in [decision_table, receipt_table, audit_table]:
                # No command, response or event from the failed transaction is durable.
                count = conn.execute(sql.SQL('SELECT count(*) FROM {} WHERE payload::text LIKE %s').format(sql.Identifier(schema, table)), ('%'+body['decision_id']+'%',)).fetchone()[0]
                assert count == 0
    finally:
        with psycopg.connect(owner_env['GOVERNANCE_STORE_DSN']) as conn:
            if failure == 'receipt':
                conn.execute(sql.SQL('ALTER TABLE {} DROP CONSTRAINT reject_receipt').format(sql.Identifier(schema, receipt_table)))
            else:
                conn.execute(sql.SQL('DROP TRIGGER fail_commit ON {}').format(sql.Identifier(schema, audit_table)))
                conn.execute(sql.SQL('DROP FUNCTION {}()').format(sql.Identifier(schema, 'fail_commit')))
    retry = post(mounted, '', owner_env, body, key)
    assert retry.status_code == 201, retry.text
    assert post(mounted, '', owner_env, body, key).json() == retry.json()


def test_persona_exact_owner_predicates_and_revocation(mounted, owner_env):
    from services.persona.write_owner import HttpGovernanceApprovalVerifier
    decision = approved(mounted, owner_env, target_type='persona_training_target', target_id='persona-isolated',
                        persona_id='persona-isolated', target_version='1', session_id='session-isolated',
                        candidate_digest='a'*64, proof_digest='b'*64)
    verifier = HttpGovernanceApprovalVerifier(base_url=mounted, service_token=token(
        owner_env['PANTHEON_GOVERNANCE_JWT_SECRET'], roles=['approval_reader']))
    target = dict(approval_decision_id=decision['decision_id'], approval_decision_ref=decision['decision_id'],
                  target_version='1', persona_id='persona-isolated', tenant_id='synthetic-tenant',
                  session_id='session-isolated', candidate_digest='a'*64, proof_digest='b'*64)
    assert verifier.verify_training_target_approval(**target)
    for field in target:
        assert not verifier.verify_training_target_approval(**{**target, field: 'wrong-'+field}), field
    revoked = post(mounted, '/'+decision['decision_id']+'/revoke', owner_env,
                   dict(expected_version=3, actor_id='synthetic-reviewer', actor_role='risk_owner'), roles=['risk_owner'])
    assert revoked.status_code == 200, revoked.text
    assert not verifier.verify_training_target_approval(**target)


def test_original_approved_receipt_survives_revocation_and_process_restart(mounted, owner_env):
    created = post(mounted, '', owner_env, proposal()).json()
    path = '/'+created['decision_id']
    assert post(mounted, path+'/review', owner_env, dict(expected_version=1, actor_id='synthetic-reviewer', actor_role='governance_reviewer')).status_code == 200
    body = dict(expected_version=2, actor_id='synthetic-reviewer', actor_role='governance_reviewer', outcome='approved', rationale='response loss')
    key = uuid.uuid4().hex
    # Discard the committed response body at the client boundary.
    with httpx.stream('POST', mounted+'/api/governance/approvals'+path+'/decide', json=body, headers=headers(owner_env, key)) as response:
        assert response.status_code == 200
    original = httpx.get(mounted+'/api/governance/approvals'+path, headers=headers(owner_env)).json()
    assert original['decision'] == 'approved' and original['version'] == 3
    revoked = post(mounted, path+'/revoke', owner_env, dict(expected_version=3, actor_id='synthetic-reviewer', actor_role='risk_owner'), roles=['risk_owner'])
    assert revoked.status_code == 200
    with server(owner_env) as restarted:
        retry = post(restarted, path+'/decide', owner_env, body, key)
        assert retry.status_code == 200 and retry.json() == original
        current = httpx.get(restarted+'/api/governance/approvals'+path, headers=headers(owner_env)).json()
        assert current['decision_state'] == 'revoked' and current['version'] == 4


@pytest.fixture(scope='module')
def owner_chain(owner_env):
    with approved_registry_owners(owner_env) as owners:
        yield owners


@pytest.mark.parametrize('invalid', ['target', 'version', 'digest', 'tenant', 'conditions', 'expired', 'revoked', 'missing'])
def test_registry_rejects_invalid_owner_decision_without_transition(owner_chain, owner_env, invalid):
    owners = owner_chain
    with httpx.Client(base_url=owners['registry_url'], headers={'Authorization': 'Bearer '+owners['registry_token']}, timeout=10) as client:
        result = client.post('/api/registry/entries', json={
            'artifact_type': 'model_artifact', 'strategy_id': 'invalid-'+uuid.uuid4().hex,
            'version': '1.0.0', 'artifact_state': 'draft', 'checksum': 'sha256:'+'a'*64,
            'lineage': {'source_run_ids': ['isolated-negative']},
            'storage_ref': {'backend': 'object_store', 'path': 'isolated.bin'}})
        assert result.status_code == 200, result.text
        identity = result.json()['entry']['registry_id']
        candidate = client.post(f'/api/registry/entries/{identity}/advance', json={'target_state': 'candidate', 'expected_artifact_state': 'draft'}).json()['entry']
        fields = dict(target_id=identity, candidate_digest=candidate['checksum'])
        if invalid == 'target': fields['target_id'] = 'wrong-target'
        if invalid == 'version': fields['target_version'] = '2.0.0'
        if invalid == 'digest': fields['candidate_digest'] = 'wrong-digest'
        if invalid == 'expired': fields['expires_at'] = '2000-01-01T00:00:00Z'
        if invalid == 'tenant':
            # A separate verified tenant owns this private decision.
            r = post(owners['governance_url'], '', owner_env, proposal(tenant_id='other-tenant', **fields), tenant_id='other-tenant')
            assert r.status_code == 201, r.text
            decision = r.json()
        elif invalid == 'conditions':
            r = post(owners['governance_url'], '', owner_env, proposal(**fields))
            decision = r.json()
            path = '/'+decision['decision_id']
            assert post(owners['governance_url'], path+'/review', owner_env, dict(expected_version=1, actor_id='synthetic-reviewer', actor_role='governance_reviewer')).status_code == 200
            r = post(owners['governance_url'], path+'/decide', owner_env, dict(expected_version=2, actor_id='synthetic-reviewer', actor_role='governance_reviewer', outcome='approved_with_conditions', conditions=['pending'], rationale='conditional proof'))
            assert r.status_code == 200
            decision = r.json()
        else:
            decision = approved(owners['governance_url'], owner_env, **fields)
        if invalid == 'revoked':
            r = post(owners['governance_url'], '/'+decision['decision_id']+'/revoke', owner_env, dict(expected_version=3, actor_id='synthetic-reviewer', actor_role='risk_owner'), roles=['risk_owner'])
            assert r.status_code == 200
        command = dict(target_state='approved', expected_artifact_state='candidate',
                       expected_version=candidate['version'], expected_updated_at=candidate['updated_at'],
                       command_key=uuid.uuid4().hex, approval_decision_id=('missing' if invalid == 'missing' else decision['decision_id']))
        rejected = client.post(f'/api/registry/entries/{identity}/advance', json=command)
        assert rejected.status_code == 400, rejected.text
        assert client.get(f'/api/registry/entries/{identity}').json()['entry'] == candidate


def test_registry_original_approval_receipt_and_evidence_after_restart(owner_chain):
    owners = owner_chain
    assert owners['entry']['approval_evidence'] == owners['decision']
    with server(owners['registry_env'], 'services.registry.service:app') as restarted:
        with httpx.Client(base_url=restarted, headers={'Authorization': 'Bearer '+owners['registry_token']}, timeout=10) as client:
            path = '/api/registry/entries/'+owners['entry']['registry_id']
            assert client.get(path).json() == owners['approved_view']
            replay = client.post(path+'/advance', json=owners['approval_command'])
            assert replay.status_code == 200, replay.text
            assert replay.json() == owners['approved_view']
            conflict = client.post(path+'/advance', json={**owners['approval_command'], 'approval_decision_id': 'another-decision'})
            assert conflict.status_code == 409, conflict.text


def test_every_mounted_approval_path_rejects_missing_principal_and_header_escalation(mounted, owner_env):
    decision = approved(mounted, owner_env)
    root = mounted+'/api/governance/approvals'
    paths = [('', 'GET', None), ('/latest-approved?target_type=registry_entry&target_id=synthetic-artifact', 'GET', None),
             ('/'+decision['decision_id'], 'GET', None), ('', 'POST', proposal())]
    for operation in ['review', 'decide', 'revoke']:
        body = dict(expected_version=3, actor_id='synthetic-reviewer', actor_role='risk_owner')
        if operation == 'decide': body.update(outcome='approved', rationale='forged')
        paths.append(('/'+decision['decision_id']+'/'+operation, 'POST', body))
    for suffix, method, body in paths:
        r = httpx.request(method, root+suffix, json=body, headers={'Idempotency-Key': uuid.uuid4().hex, 'X-Actor-Role': 'risk_owner', 'X-Tenant-Id': 'synthetic-tenant'})
        assert r.status_code == 401, (method, suffix, r.text)
