# PPL-GOV-002 - BFF Promotion Review Routes

Owner: Claude2
Reviewer: Codex
Depends on: PPL-GOV-001
Type: BFF implementation task

## Purpose

Provide the missing management API that lets Human Inbox and Human Gate review
paper-to-canary, canary-to-live, and live ranking recommendations.

## Scope

- Add `GET /bff/management/promotion-reviews`.
- Add `GET /bff/management/promotion-reviews/{review_id}`.
- Add `POST /bff/management/promotion-reviews/{review_id}/decisions`.
- Support `approve`, `approve_with_conditions`, and `reject`.
- Require approver/admin role for decisions.
- Require reject rationale.
- Preserve idempotency for decision writes.
- Return audit/receipt fields that frontend can display.
- Do not place orders or mutate live capital from these routes.

## Acceptance

- Missing auth returns 401.
- Read roles can list/detail reviews.
- Operator-only users cannot approve live promotion decisions.
- Approver/admin can approve, approve with conditions, or reject.
- Reject without rationale returns validation error.
- Duplicate idempotency key returns stable result.
- Response includes review id, persona id, from stage, target stage,
  recommendation id, decision status, audit event, and live capital mutation
  false.

## Validation

```sh
python3 -m pytest services/control-plane/bff/tests/test_bff_promotion_reviews.py
python3 -m pytest services/control-plane/bff/tests/test_bff_b5_humangate_commands.py
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py
git diff --check
```
