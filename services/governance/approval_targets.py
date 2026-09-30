"""Subject binding and decider-count rules shared by ApprovalDecision and ApprovalEvidence.
Subject: metadata['subject']; approving deciders: metadata['approvals']."""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Mapping, Optional

from services.governance.write_authority import is_authorized_to_decide

SUBJECT_FIELDS = {
    'rebalance_apply': ('plan_id', 'plan_digest', 'capital_pool_id'),
    'capital_binding_activation': ('binding_id', 'persona_id', 'capital_pool_id', 'risk_direction'),
    'persona_lifecycle_transition': ('persona_id', 'from_state', 'to_state'),
    'evolution_execute': ('proposal_id', 'proposal_content_digest'),
}
RISK_DIRECTIONS = frozenset({'increase', 'reduce', 'neutral'})


def target_name(target_type: Any) -> str:
    return str(getattr(target_type, 'value', target_type))


def is_action_target(target_type: Any) -> bool:
    return target_name(target_type) in SUBJECT_FIELDS


def subject_of(metadata: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    subject = (metadata or {}).get('subject')
    return subject if isinstance(subject, Mapping) else {}


def approvals(metadata: Optional[Mapping[str, Any]]) -> List[Any]:
    votes = (metadata or {}).get('approvals')
    return votes if isinstance(votes, list) else []


def approvers(metadata: Optional[Mapping[str, Any]]) -> List[Any]:
    return [a.get('actor_id') for a in approvals(metadata) if isinstance(a, Mapping)]

def parse_expiry(value: Any) -> datetime:
    """A vote expiry must be a timezone-aware ISO instant."""
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is not None:
            return instant
    except (AttributeError, ValueError):
        pass
    raise ValueError('vote expires_at must be a valid timezone-aware timestamp')


def merged_constraints(metadata: Optional[Mapping[str, Any]]) -> tuple:
    """Union of vote conditions and earliest vote expiry (by instant); the final decision may not broaden them."""
    votes = [a for a in approvals(metadata) if isinstance(a, Mapping)]
    conditions = [c for a in votes for c in a.get('conditions') or []]
    expiries = [a['expires_at'] for a in votes if a.get('expires_at') is not None]
    return list(dict.fromkeys(conditions)), min(expiries, key=parse_expiry) if expiries else None


def required_deciders(target_type: Any, metadata: Optional[Mapping[str, Any]]) -> int:
    """A risk-increasing capital target needs two distinct deciders."""
    if target_name(target_type) == 'capital_binding_activation' \
            and subject_of(metadata).get('risk_direction') == 'increase':
        return 2
    return 1


def subject_errors(target_type: Any, metadata: Optional[Mapping[str, Any]]) -> List[str]:
    subject = subject_of(metadata)
    errors = [f'subject.{name} is required' for name in SUBJECT_FIELDS.get(target_name(target_type), ())
              if not isinstance(subject.get(name), str) or not subject[name].strip()]
    if 'risk_direction' in subject and subject['risk_direction'] not in RISK_DIRECTIONS:
        errors.append('subject.risk_direction must be increase, reduce or neutral')
    return errors


def evidence_errors(target_type: Any, metadata: Optional[Mapping[str, Any]], proposer: Any,
                    risk_level: Any, expires_at: Any, now: datetime) -> List[str]:
    """Errors that make a decided approval unusable for its action target."""
    errors = subject_errors(target_type, metadata)
    votes, risk = approvals(metadata), target_name(risk_level)
    bad = [v for v in votes if not (isinstance(v, Mapping) and isinstance(v.get('actor_id'), str)
                                    and v['actor_id'].strip() and is_authorized_to_decide(v.get('actor_role'), risk))]
    if bad or not isinstance((metadata or {}).get('approvals', []), list):
        errors.append('approvals must be distinct identified deciders with authorized roles')
    ids = [v['actor_id'] for v in votes if v not in bad]
    named = approvers(metadata)
    try:
        conditions, expiry = merged_constraints(metadata)
        if conditions or (expiry is not None and not now < parse_expiry(expiry) == parse_expiry(expires_at)):
            errors.append('vote conditions or expiry are inconsistent with the decision or expired')
    except ValueError as exc:
        errors.append(str(exc))
    if not isinstance(proposer, str) or not proposer.strip():
        errors.append('proposer identity is required')
    elif proposer in named:
        errors.append('decider must not be the proposer')
    if len(set(ids)) < required_deciders(target_type, metadata):
        errors.append('insufficient distinct deciders')
    return errors
