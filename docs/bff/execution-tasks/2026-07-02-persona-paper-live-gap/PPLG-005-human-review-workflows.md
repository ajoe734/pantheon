# PPLG-005 - Human Review For Canary, Live, And Quarterly Ranking

Priority: P0

Area: Governance review, approval decisions, allocation changes

Depends on: `PPLG-004`

## Goal

Implement human review workflows so promotion to canary, canary to live, live
allocation changes, quarterly replacement, and resume-after-incident all require
explicit human decisions.

## Required Work

- Implement review request creation from promotion recommendations.
- Implement review queue and detail endpoints.
- Implement decision endpoints for approve, approve with conditions, and reject.
- Implement quarterly ranking snapshots and rebalance proposals.
- Block canary/live/runtime allocation changes without matching decision scope.
- Include reviewer, approval scope, max allocation, conditions, rollback target,
  risk notes, and expiry.

## Acceptance Criteria

- Paper recommendation alone cannot create canary/live RuntimeBinding.
- Canary and live approvals require decision records.
- Quarterly ranking proposal cannot rebalance without human decision.
- Replacement proposal requires challenger evidence and incumbent comparison.
- Resume from `risk_off` or `frozen` requires human decision.
- Tests cover approve, approve with conditions, reject, expired approval, and
  missing approval.

## Artifacts

- `services/control-plane/governance/approval_decision.py`
- `services/control-plane/governance/deployment_plan.py`
- `services/control-plane/bff/*review*`
- `services/control-plane/bff/tests/*promotion_review*`
- `services/control-plane/bff/tests/*quarterly*`
