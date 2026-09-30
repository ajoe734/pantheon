"""Action-bound approval targets: subject binding, proposer separation, decider counts."""
import importlib.util
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
    'evolution_execute': {'proposal_id': 'e1', 'proposal_content_digest': 'sha256:b'},
}
TYPES = list(SUBJECTS)


def proposed(target_type, subject=None, risk='low'):
    return ad.ApprovalDecision.create_proposed(
        'd1', target_type, 't1', '1', risk_level=risk, tenant_id='tenant-unit',
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
