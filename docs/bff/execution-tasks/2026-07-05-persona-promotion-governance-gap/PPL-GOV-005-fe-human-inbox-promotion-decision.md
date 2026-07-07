# PPL-GOV-005 - Human Inbox Promotion Decision UX

Owner: Claude
Reviewer: Codex
Depends on: PPL-GOV-002, PPL-GOV-004
Type: frontend implementation task

## Purpose

Make the approval location obvious and complete: Human Inbox / Human Gate detail
is where a human approves or rejects paper-to-canary, canary-to-live, and live
ranking reviews.

## Scope

- Ensure promotion-review inbox items display source recommendation, persona,
  current stage, target stage, required evidence, required roles, and decision
  status.
- Keep approve, approve-with-conditions, and reject controls wired to BFF.
- Require rationale for reject.
- Show decision receipt/audit id after success.
- Keep live capital mutation status explicit.

## Acceptance

- Promotion review detail answers: what is being approved, why, by whom, and
  what happens next.
- Approve and approve-with-conditions submit to BFF and render receipt.
- Reject requires rationale and renders receipt.
- Missing BFF route or write-disabled mode does not show fake success.
- Tests cover all decision states and error states.

## Validation

```sh
npm test -- src/management/pages/oversight/HumanGateDetail.test.tsx
npm test -- src/lib/bff-v1/__tests__/management.test.ts
npm run lint
git diff --check
```
