"""Focused common-reader contract tests and explicitly injected unit snapshots.

The snapshot reader is only a transport double. Production DTO parsing and
validity predicates still execute; real HTTP/Postgres proofs live alongside.
"""
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid


def approval_snapshot(**values):
    body = dict(decision_id='approval-unit', tenant_id='tenant-unit', target_type='registry_entry',
                target_id='artifact-unit', target_version='1.0.0', decision_state='decided',
                decision='approved', actor_id='unit-reviewer', actor_role='governance_reviewer',
                risk_level='low', decided_at='2026-01-01T00:00:00Z', expires_at='2099-01-01T00:00:00Z',
                recorded_at='2026-01-01T00:00:00Z', authority_status='authoritative',
                controller_record_ref='governance-controller://approval-unit', version=3,
                event_id='unit-decision-event', conditions=[])
    body.update(values)
    return body


class SnapshotApprovalReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get(self, decision_id):
        body = self.snapshot() if callable(self.snapshot) else self.snapshot
        try:
            evidence = ApprovalEvidence.model_validate(body)
        except ValueError as exc:
            raise ApprovalInvalid('Malformed Governance decision') from exc
        if evidence.decision_id != decision_id:
            raise ApprovalInvalid('Governance exact decision ID mismatch')
        return evidence

    def verify(self, decision_id, *, expected, now=None):
        return self.get(decision_id).require_valid(expected=expected, now=now)
