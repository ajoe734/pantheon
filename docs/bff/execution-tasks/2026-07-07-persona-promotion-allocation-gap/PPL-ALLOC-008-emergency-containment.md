# PPL-ALLOC-008 - Emergency Containment Policy

Owner: Antigravity2
Reviewer: Claude2
Depends on: `PPL-ALLOC-001`, `PPL-ALLOC-004`
Type: policy/BFF/frontend guard task

## Problem

Quarterly ranking is too slow for hard losses, hard risk breaches, forced kill
events, or binding mismatches. Emergency containment must act immediately, but
it must never become a hidden promotion or capital-increase path.

## Scope

- Encode emergency triggers from the gap spec.
- Add BFF containment command/review behavior for freeze, reduce capital,
  suspend, risk-off, rollback, and retire.
- Add UI surfacing in Promotion & Allocation and Sentinel/Risk Center.
- Require role gates, reason, evidence refs, idempotency, and audit receipts.
- Prove emergency actions cannot promote, create canary/live, or increase live
  capital.

## Acceptance

- Tests cover drawdown breach, hard risk breach, reconciliation anomaly,
  runtime/binding mismatch, missing telemetry, and unresolved incident.
- Positive containment actions emit audit receipts and rollback references
  where applicable.
- Attempted emergency promotion or allocation increase is rejected.
- UI copy and buttons show containment, not promotion.

## Validation

```sh
git status -sb
python3 -m pytest services/control-plane/bff/tests/test_bff_emergency_containment.py -q
npm test -- src/management/pages/v5/Sentinel.test.tsx
npm test -- src/management/pages/oversight/PromotionAllocation.test.tsx
git diff --check
```
