"""Action-bound approval targets: subject binding, proposer separation, decider counts."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid
from services.governance.test_approval_authority import approval_snapshot

_path = Path(__file__).resolve().parents[1] / 'control-plane' / 'governance' / 'approval_decision.py'
_spec = importlib.util.spec_from_file_location('approval_decision_targets_under_test', _path)
ad = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ad
_spec.loader.exec_module(ad)

SUBJECTS = {
    'rebalance_apply': {'plan_id': 'p1', 'plan_digest': 'sha256:a', 'capital_pool_id': 'pool1',
                        'risk_direction': 'reduce'},
    'capital_binding_activation': {'binding_id': 'b1', 'persona_id': 'per1', 'capital_pool_id': 'pool1',
                                   'risk_direction': 'reduce'},
    'persona_lifecycle_transition': {'persona_id': 'per1', 'from_state': 'paper', 'to_state': 'live'},
    'paper_canary_candidacy': {'persona_id': 'per1', 'from_stage': 'paper', 'to_stage': 'canary_candidate',
                              'quarter': '2026-Q3', 'ranking_snapshot_id': 'snap1', 'proposal_content_digest': 'a' * 64},
    'evolution_execute': {'proposal_id': 'e1', 'proposal_content_digest': 'sha256:b'},
}
TYPES = list(SUBJECTS)


def proposed(target_type, subject=None, risk='low'):
    return ad.ApprovalDecision.create_proposed(
        'd1', target_type, 'per1' if target_type == 'paper_canary_candidacy' else 't1',
        'snap1' if target_type == 'paper_canary_candidacy' else '1',
        proposal_content_digest='a' * 64 if target_type == 'paper_canary_candidacy' else None,
        risk_level=risk, tenant_id='tenant-unit',
        owner_user_id='proposer', subject=SUBJECTS[target_type] if subject is None else subject)


def decide(decision, actor='decider-a', outcome='approved', role='governance_reviewer'):
    decision.accept_review(role, actor)
    decision.decide(outcome, 'ok', actor_role=role, actor_id=actor, expires_at='2099-01-01T00:00:00Z')
    return decision


def snapshot(decision):
    return approval_snapshot(**{k: v for k, v in decision.to_dict().items() if k in {
        'target_type', 'target_id', 'target_version', 'decision_state', 'decision', 'actor_id', 'actor_role',
        'risk_level', 'decided_at', 'expires_at', 'recorded_at', 'authority_status', 'controller_record_ref',
        'metadata', 'owner_user_id', 'tenant_id', 'conditions'}})


@pytest.mark.parametrize('target_type', TYPES)
def test_valid_and_subject_match(target_type):
    evidence = ApprovalEvidence.model_validate(snapshot(decide(proposed(target_type))))
    subject_key = next(iter(SUBJECTS[target_type]))
    evidence.require_valid(expected={'tenant_id': 'tenant-unit', 'target_type': target_type,
                                     f'subject.{subject_key}': SUBJECTS[target_type][subject_key]})


@pytest.mark.parametrize('target_type', TYPES)
def test_mismatched_subject_tenant_or_type_rejected(target_type):
    evidence = ApprovalEvidence.model_validate(snapshot(decide(proposed(target_type))))
    subject_key = next(iter(SUBJECTS[target_type]))
    for expected in ({f'subject.{subject_key}': 'other'}, {'tenant_id': 'other'}, {'target_type': 'registry_entry'}):
        with pytest.raises(ApprovalInvalid):
            evidence.require_valid(expected=expected)


@pytest.mark.parametrize('target_type', TYPES)
def test_self_approval_rejected(target_type):
    decision = proposed(target_type)
    decision.accept_review('governance_reviewer', 'proposer')
    with pytest.raises(ValueError, match='proposer'):
        decision.decide('approved', 'ok', actor_role='governance_reviewer', actor_id='proposer')
    forged = snapshot(decide(proposed(target_type)))
    forged['metadata']['approvals'] = [{'actor_id': 'proposer'}]
    with pytest.raises(ApprovalInvalid, match='proposer'):
        ApprovalEvidence.model_validate(forged).require_valid()


@pytest.mark.parametrize('target_type', TYPES)
def test_missing_subject_rejected(target_type):
    with pytest.raises(ValueError, match='subject'):
        decide(proposed(target_type, subject={}))


def test_risk_increasing_capital_needs_two_distinct_deciders():
    subject = {**SUBJECTS['capital_binding_activation'], 'risk_direction': 'increase'}
    decision = proposed('capital_binding_activation', subject=subject, risk='high')
    decide(decision, 'risk-a', role='risk_owner')
    assert decision.decision_state == ad.DecisionState.UNDER_REVIEW
    with pytest.raises(ValueError, match='already approved'):
        decision.decide('approved', 'again', actor_role='risk_owner', actor_id='risk-a')
    insufficient = snapshot(decision) | {'decision_state': 'decided', 'decision': 'approved', 'actor_id': 'risk-a',
                                         'actor_role': 'risk_owner',
                                         **{k: approval_snapshot()[k] for k in ('decided_at', 'expires_at', 'recorded_at', 'authority_status', 'controller_record_ref')}}
    with pytest.raises(ApprovalInvalid, match='insufficient'):
        ApprovalEvidence.model_validate(insufficient).require_valid()
    decision.decide('approved', 'second', actor_role='risk_owner', actor_id='risk-b',
                    expires_at='2099-01-01T00:00:00Z')
    assert decision.decision_state == ad.DecisionState.DECIDED
    ApprovalEvidence.model_validate(snapshot(decision)).require_valid()


def test_risk_increasing_rebalance_needs_two_distinct_deciders():
    subject = {**SUBJECTS['rebalance_apply'], 'risk_direction': 'increase'}
    decision = proposed('rebalance_apply', subject=subject, risk='high')
    decide(decision, 'risk-a', role='risk_owner')
    assert decision.decision_state == ad.DecisionState.UNDER_REVIEW
    with pytest.raises(ValueError, match='already approved'):
        decision.decide('approved', 'again', actor_role='risk_owner', actor_id='risk-a')
    body = snapshot(decision) | {'decision_state': 'decided', 'decision': 'approved', 'actor_id': 'risk-a',
                                 'actor_role': 'risk_owner',
                                 **{k: approval_snapshot()[k] for k in ('decided_at', 'expires_at', 'recorded_at', 'authority_status', 'controller_record_ref')}}
    with pytest.raises(ApprovalInvalid, match='insufficient'):
        ApprovalEvidence.model_validate(body).require_valid()
    decision.decide('approved', 'second', actor_role='risk_owner', actor_id='risk-b',
                    expires_at='2099-01-01T00:00:00Z')
    assert decision.decision_state == ad.DecisionState.DECIDED
    ApprovalEvidence.model_validate(snapshot(decision)).require_valid()


def test_rebalance_missing_risk_classification_rejected():
    subject = {k: v for k, v in SUBJECTS['rebalance_apply'].items() if k != 'risk_direction'}
    with pytest.raises(ValueError, match='risk_direction'):
        decide(proposed('rebalance_apply', subject=subject))


@pytest.mark.parametrize('target_type', TYPES)
def test_expiry_and_revocation_rejected(target_type):
    body = snapshot(decide(proposed(target_type)))
    for change in ({'expires_at': '2000-01-01T00:00:00Z'}, {'revoked_at': '2026-02-01T00:00:00Z'}):
        with pytest.raises(ApprovalInvalid):
            ApprovalEvidence.model_validate(body | change).require_valid()


def _two_decider_body():
    subject = {**SUBJECTS['capital_binding_activation'], 'risk_direction': 'increase'}
    decision = proposed('capital_binding_activation', subject=subject, risk='high')
    decision.accept_review('risk_owner', 'risk-a')
    decision.decide('approved_with_conditions', 'ok', actor_role='risk_owner', actor_id='risk-a',
                    conditions=['maximum allocation 1%'], expires_at='2030-01-01T00:00:00Z')
    return decision


def test_later_unconditional_vote_cannot_broaden_conditions_or_expiry():
    decision = _two_decider_body()
    decision.decide('approved', 'ok', actor_role='risk_owner', actor_id='risk-b', expires_at='2099-01-01T00:00:00Z')
    assert decision.conditions == ['maximum allocation 1%']
    assert decision.expires_at == '2030-01-01T00:00:00Z'
    assert decision.decision == ad.DecisionOutcome.APPROVED_WITH_CONDITIONS


def test_vote_expiry_compared_as_instants_and_never_broadened():
    decision = _two_decider_body()
    decision.metadata['approvals'][0]['expires_at'] = '2030-01-01T00:00:00+14:00'  # 2029-12-31T10:00Z
    decision.decide('approved', 'ok', actor_role='risk_owner', actor_id='risk-b', expires_at='2029-12-31T23:00:00Z')
    assert decision.expires_at == '2030-01-01T00:00:00+14:00'


@pytest.mark.parametrize('bad', ['not-a-date', '2099-01-01T00:00:00', 5])
def test_malformed_vote_expiry_rejected(bad):
    decision = _two_decider_body()
    with pytest.raises(ValueError):
        decision.decide('approved', 'ok', actor_role='risk_owner', actor_id='risk-b', expires_at=bad)
    decision.metadata['approvals'][0]['expires_at'] = bad
    assert any('expires_at' in e for e in ad.approval_targets.evidence_errors(
        decision.target_type, decision.metadata, 'proposer', 'high', '2099-01-01T00:00:00Z',
        datetime(2026, 1, 1, tzinfo=timezone.utc)))


@pytest.mark.parametrize('target_type', TYPES)
@pytest.mark.parametrize('approvals', [
    [{}, {'actor_id': 'risk-b', 'actor_role': 'risk_owner'}],
    [{'actor_id': ' ', 'actor_role': 'risk_owner'}, {'actor_id': 'risk-b', 'actor_role': 'risk_owner'}],
    [{'actor_id': 'gate', 'actor_role': 'automated_gate'}, {'actor_id': 'risk-b', 'actor_role': 'risk_owner'}],
    'not-a-list',
])
def test_malformed_or_unauthorized_votes_fail_closed(target_type, approvals):
    subject = {**SUBJECTS[target_type], **({'risk_direction': 'increase'} if 'risk_direction' in SUBJECTS[target_type] else {})}
    decision = decide(proposed(target_type, subject=subject, risk='high'), role='risk_owner')
    if decision.decision_state != ad.DecisionState.DECIDED:
        decision.decide('approved', 'ok', actor_role='risk_owner', actor_id='risk-b', expires_at='2099-01-01T00:00:00Z')
    body = snapshot(decision)
    body['metadata']['approvals'] = approvals
    with pytest.raises(ApprovalInvalid, match='approvals|insufficient'):
        ApprovalEvidence.model_validate(body).require_valid()


@pytest.mark.parametrize('target_type', TYPES)
@pytest.mark.parametrize('change', [
    lambda m: m['approvals'][0].update(expires_at='2000-01-01T00:00:00Z'),
    lambda m: m['approvals'][0].update(conditions=['maximum allocation 1%']),
    lambda m: m.pop('approvals'),
])
def test_vote_level_expiry_and_conditions_enforced(target_type, change):
    body = snapshot(decide(proposed(target_type)))
    change(body['metadata'])
    with pytest.raises(ApprovalInvalid):
        ApprovalEvidence.model_validate(body).require_valid()


def test_expired_first_vote_not_masked_by_later_top_level_expiry():
    decision = _two_decider_body()
    decision.metadata['approvals'][0]['conditions'] = []
    decision.decide('approved', 'ok', actor_role='risk_owner', actor_id='risk-b', expires_at='2099-01-01T00:00:00Z')
    body = snapshot(decision) | {'conditions': [], 'decision': 'approved', 'expires_at': '2099-01-01T00:00:00Z'}
    with pytest.raises(ApprovalInvalid):
        ApprovalEvidence.model_validate(body).require_valid(now=datetime(2031, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize('target_type', TYPES)
@pytest.mark.parametrize('owner', [None, '', '  '])
def test_missing_proposer_identity_rejected(target_type, owner):
    body = snapshot(decide(proposed(target_type))) | {'owner_user_id': owner}
    with pytest.raises(ApprovalInvalid, match='proposer'):
        ApprovalEvidence.model_validate(body).require_valid()


@pytest.mark.parametrize('change', [
    {'from_stage': 'canary'}, {'to_stage': 'live'}, {'quarter': '2026-Q5'},
    {'ranking_snapshot_id': 'other'}, {'persona_id': 'other'}, {'proposal_content_digest': 'bad'},
])
def test_candidacy_subject_binds_stages_quarter_target_snapshot_and_digest(change):
    subject = {'persona_id': 'p1', 'from_stage': 'paper', 'to_stage': 'canary_candidate', 'quarter': '2026-Q3',
               'ranking_snapshot_id': 'snap1', 'proposal_content_digest': 'a' * 64}
    decision = ad.ApprovalDecision.create_proposed(
        'd1', 'paper_canary_candidacy', 'p1', 'snap1', tenant_id='t1', owner_user_id='evaluator',
        proposal_content_digest='a' * 64, subject={**subject, **change})
    assert decision.validate()


# The whole schedule -> Inbox -> decision journey uses real PostgreSQL owner stores.
# The only business seam is HTTP: external owner facts and the unavailable agent,
# plus routing internal owner HTTP to the in-process production applications.
from services.governance.test_approval_authority_postgres import owner_env  # noqa: E402,F401


@pytest.mark.parametrize('case,outcome', [('eligible', 'approved'), ('eligible', 'rejected'),
                                          ('tie', None), ('insufficient', None)])
def test_scheduled_paper_candidacy_inbox_decision_journey(owner_env, monkeypatch, tmp_path, case, outcome):
    import io
    import importlib
    import urllib.error
    import urllib.request
    from urllib.parse import urlparse, parse_qs
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.governance import main as gov
    from services.governance.pg_store import PostgresApprovalDecisionStore, PostgresGovernanceAuditStore
    from services.rankings.store import RankingWriteStore
    from services.rankings.snapshots import snapshot_record, admit_snapshot
    from services.control_plane.bff.auth import policy
    from services.control_plane.bff.core.errors import register_error_handlers
    from services.control_plane.bff.core.owner_reads import OwnerReadContextMiddleware
    from services.control_plane.bff.ports import create_read_surface_ports
    from services.control_plane.bff.ports.persona_write_owner import create_persona_registry_write_owner
    from services.control_plane.bff.ports.rankings import RankingSnapshotReadPort
    from services.control_plane.bff.personas import PersonaService, create_personas_router
    from services.control_plane.bff.management_read_models import create_management_router
    from services.control_plane.bff.governance.router import create_governance_router
    from services.control_plane.bff.command_queue import CommandStore
    from services.runtime_auth_inbound import encode_jwt_hs256
    import uuid

    evaluator_path = Path(__file__).resolve().parents[1] / 'persona-evaluator-agent'
    monkeypatch.syspath_prepend(str(evaluator_path))
    pea = importlib.import_module('persona_evaluator_agent')
    fixture = importlib.import_module('test_persona_evaluator_agent')
    schema = 'canary_' + uuid.uuid4().hex
    rankings = RankingWriteStore(owner_env['GOVERNANCE_STORE_DSN'], schema + '.rankings')
    item = fixture._promotion_item(days=29 if case == 'insufficient' else 30)
    items = [item, fixture._promotion_item('p2')] if case == 'tie' else [item]
    snapshot = admit_snapshot(rankings, snapshot_record(items, surface='quarterly', period='2026-Q3'))
    state_path = tmp_path / 'evaluator.json'
    evaluator_store = pea.Store(state_path)
    evaluator_store.update(lambda s: s.update(latest={'2026-Q3': snapshot.ranking_snapshot_id}, results={
        f'2026-Q3|{snapshot.ranking_snapshot_id}': {'items': [], 'run_id': 'quarter-close', 'evaluated_at': '2026-09-30T23:59:59Z'}}))

    secret = 'isolated-canary-integration-test-secret'
    env = {
        'PANTHEON_BFF_AUTH_STUB': '', 'PANTHEON_BFF_AUTH_MODE': 'strict', 'PANTHEON_BFF_MFA_REQUIRED': 'false',
        'PANTHEON_BFF_JWT_SECRET': secret, 'PANTHEON_BFF_JWT_ISSUER': 'canary-test', 'PANTHEON_BFF_JWT_AUDIENCE': 'canary-test',
        'PANTHEON_GOVERNANCE_JWT_SECRET': secret, 'PANTHEON_GOVERNANCE_JWT_ISSUER': 'canary-test',
        'PANTHEON_GOVERNANCE_JWT_AUDIENCE': 'canary-test',
        'RANKING_STORE_DSN': owner_env['GOVERNANCE_STORE_DSN'], 'RANKING_STORE_TABLE': schema + '.rankings',
        'PANTHEON_GOVERNANCE_APPROVAL_API_URL': 'http://gov.test', 'PANTHEON_GOVERNANCE_API_URL': 'http://gov.test',
        'PERSONA_URL': 'http://facts.test', 'PANTHEON_PERSONA_SERVICE_TOKEN': 'persona-test',
        'PANTHEON_CAPITAL_API_URL': 'http://facts.test', 'PANTHEON_DEPLOYMENT_API_URL': 'http://facts.test',
        'PANTHEON_RUNTIME_MANAGER_URL': 'http://facts.test', 'PANTHEON_TELEMETRY_API_URL': 'http://facts.test',
        'PANTHEON_REGISTRY_API_URL': 'http://facts.test',
        'PANTHEON_EVOLUTION_API_URL': 'http://facts.test', 'PANTHEON_INCIDENTS_API_URL': 'http://facts.test',
        'PERSONA_EVALUATOR_URL': 'http://evaluator.test', 'PERSONA_EVALUATOR_READ_TOKEN': 'read-test',
        'PERSONA_EVALUATOR_STATE_PATH': str(state_path), 'PERSONA_EVALUATOR_PORT': '0', 'PERSONA_EVALUATOR_ONCE': '1',
        'PERSONA_EVALUATOR_BFF_URL': 'http://bff.test', 'PERSONA_EVALUATOR_PERSONA_URL': 'http://facts.test',
        'PERSONA_EVALUATOR_PERSONA_TOKEN': 'persona-test', 'PERSONA_EVALUATOR_PAPER_POOL_ID': '',
        'PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL': 'http://agent.test', 'PANTHEON_TENANT_ID': 't1',
        'PERSONA_EVALUATOR_ACTOR_ID': 'evaluator',
    }
    def jwt(actor, roles):
        return encode_jwt_hs256({'sub': actor, 'roles': roles, 'tenant_id': 't1', 'exp': 4102444800,
                                'iss': 'canary-test', 'aud': 'canary-test'}, secret=secret)
    decider = jwt('human-reviewer', ['operator', 'governance_reviewer', 'admin'])
    env['PERSONA_EVALUATOR_GOVERNANCE_TOKEN'] = jwt('evaluator', ['approval_proposer'])
    env['PERSONA_EVALUATOR_BFF_TOKEN'] = decider
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key in ('PERSONA_EVALUATOR_BFF_TOKEN_FILE', 'PERSONA_EVALUATOR_GOVERNANCE_TOKEN_FILE',
                'PERSONA_EVALUATOR_PERSONA_TOKEN_FILE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(gov, 'store', PostgresApprovalDecisionStore(owner_env['GOVERNANCE_STORE_DSN'], schema + '.decisions'))
    monkeypatch.setattr(gov, 'audit_store', PostgresGovernanceAuditStore(owner_env['GOVERNANCE_STORE_DSN'], schema + '.audit'))
    governance = TestClient(gov.app)
    persona_owner = create_persona_registry_write_owner()
    read_store = create_read_surface_ports(persona_registry_store=persona_owner, ranking_store=rankings)
    app = FastAPI()
    app.add_middleware(OwnerReadContextMiddleware)
    register_error_handlers(app)
    commands = CommandStore(str(tmp_path / 'commands.jsonl'))
    app.include_router(create_personas_router(service=PersonaService(
        read_store=read_store, write_owner=persona_owner, ranking_write_owner=RankingSnapshotReadPort(rankings), command_store=commands)))
    app.include_router(create_management_router(get_read_store=lambda: read_store))
    app.include_router(create_governance_router(read_surface=read_store, extract_identity=policy.extract_identity_jwt))
    bff = TestClient(app)
    calls = []

    class Response:
        def __init__(self, status, content):
            self.status, self.body, self.headers = status, io.BytesIO(content), self
        def read(self): return self.body.read()
        def get_content_type(self): return 'application/json'
        def __enter__(self): return self
        def __exit__(self, *_): return False

    personas = [{'persona_id': i['persona_id'], 'id': i['persona_id'], 'tenant_id': 't1', 'lifecycle_state': 'paper_running',
                 'metadata': {}, 'name': i['persona_id']} for i in items]
    def transport(req, timeout=30):
        url = urlparse(req.full_url)
        path = url.path + ('?' + url.query if url.query else '')
        method = req.get_method()
        calls.append((url.netloc, method, url.path, json.loads(req.data) if req.data else None))
        if url.netloc in ('gov.test', 'bff.test'):
            client = governance if url.netloc == 'gov.test' else bff
            response = client.request(method, path, headers=dict(req.header_items()), content=req.data)
            if response.status_code >= 400:
                raise urllib.error.HTTPError(req.full_url, response.status_code, 'owner error', {}, io.BytesIO(response.content))
            return Response(response.status_code, response.content)
        if url.netloc == 'agent.test':
            raise OSError('agent unavailable')
        if url.netloc == 'evaluator.test':
            query = parse_qs(url.query)
            quarter = query['quarter'][0]
            state = evaluator_store.load()
            sid = (query.get('snapshot_id') or [state['latest'].get(quarter, '')])[0]
            return Response(200, json.dumps({'data': {'result': pea.saved_result(state, quarter, sid)}}).encode())
        assert url.netloc == 'facts.test', f'unexpected owner call {req.full_url}'
        assert method == 'GET' or (method == 'POST' and url.path.endswith('trade-reflections:pattern-review'))
        if url.path == '/api/registry/strategy-specs' and 'persona_binding' in url.query:
            body = {}
        elif url.path == '/api/personas':
            body = personas
        elif url.path in {f'/api/personas/{p["persona_id"]}' for p in personas}:
            body = next(p for p in personas if url.path.endswith('/' + p['persona_id']))
        elif url.path.endswith('trade-reflections:pattern-review'):
            body = {'status': 'no_op'}
        else:
            body = []
        return Response(200, json.dumps(body).encode())
    monkeypatch.setattr(urllib.request, 'urlopen', transport)
    monkeypatch.setattr(persona_owner, '_opener', transport)
    # Track the real HTTP server solely to close this scheduler test's resource.
    servers = []
    original_serve = pea.serve
    def start_server(*args):
        server = original_serve(*args)
        servers.append(server)
        return server
    monkeypatch.setattr(pea, 'serve', start_server)
    try:
        pea.main()  # real production entry point; current-quarter failure does not veto quarter-close nomination
        first = evaluator_store.load()['last_promotion']
        if outcome is None:
            assert first['status'] == 'no_nomination', first
            assert first['reason'] == ('rank_one_tie' if case == 'tie' else 'insufficient_quarter_sample')
            assert not [c for c in calls if c[0] == 'gov.test' and c[1] == 'POST']
            return
        assert first['status'] == 'proposed', first
        decision_id = first['decision_id']
        headers = {'Authorization': 'Bearer ' + decider}
        inbox = bff.get('/bff/management/human-inbox', headers=headers)
        assert inbox.status_code == 200, inbox.text
        approval = next(i for i in inbox.json()['data']['items'] if i.get('approval_decision_id') == decision_id)
        assert 'observed_trading_days' in approval['summary']
        detail = bff.get(f'/bff/approvals/{decision_id}', headers=headers)
        assert detail.json()['data']['allowedActions']['canApprove'] is True, detail.text
        reviews = bff.get('/bff/management/promotion-reviews?quarter=2026-Q3', headers=headers)
        assert reviews.status_code == 200, reviews.text
        assert reviews.json()['data']['items'], (reviews.text, [(c[0], c[2]) for c in calls])
        assert reviews.json()['data']['items'][0]['status'] == 'pending_human_gate', reviews.text
        decided = bff.post(f'/bff/approvals/{decision_id}/decide', headers={**headers, 'Idempotency-Key': 'human-decision'},
                           json={'decision': outcome, 'rationale': 'human reviewed nomination only', 'actor_role': 'governance_reviewer',
                                 'expected_version': 1})
        assert decided.status_code == 202, decided.text
        reviews = bff.get('/bff/management/promotion-reviews?quarter=2026-Q3', headers=headers)
        review = reviews.json()['data']['items'][0]
        assert review['status'] == ('decision_accepted' if outcome == 'approved' else 'decision_rejected'), reviews.text
        assert review['human_inbox_id'] is None
        assert reviews.json()['data']['summary']['decision_' + ('accepted' if outcome == 'approved' else 'rejected') + '_count'] == 1
        pea.main()  # rerun uses the same proposal, including after a reject
        assert evaluator_store.load()['last_promotion']['reused'] is True
        assert len([c for c in calls if c[:3] == ('gov.test', 'POST', '/api/governance/approvals')]) == 1
        assert [(c[0], c[2]) for c in calls if c[1] == 'POST' and not c[2].endswith('trade-reflections:pattern-review')] == [
            ('agent.test', '/api/openclaw-adapter/assistant/providers/openclaw/structured'),
            ('gov.test', '/api/governance/approvals'),
            ('gov.test', f'/api/governance/approvals/{decision_id}/decide')]
        row = governance.get(f'/api/governance/approvals/{decision_id}', headers=headers).json()
        assert row['decision'] == outcome and row['target_type'] == 'paper_canary_candidacy'
        # Existing consumers require their exact target type; candidacy cannot authorize lifecycle or capital.
        evidence = ApprovalEvidence.model_validate(row)
        for target_type in ('persona_lifecycle_transition', 'rebalance_apply', 'capital_binding_activation'):
            with pytest.raises(ApprovalInvalid):
                evidence.require_valid(expected={'target_type': target_type})
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
