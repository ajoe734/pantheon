# TJ-E2E-008 - Governed Journey Actions

Owner: Antigravity
Reviewer: Claude
Wave: 3
Repository: `ajoe734/pantheon` and `ajoe734/execute-plans`
Dependencies: `TJ-E2E-005`, `TJ-E2E-006`

## Goal

Add contextual escalate, human review, pause, cancel, reconciliation retry and
incident acknowledgement without creating a governance bypass.

## Required work and acceptance

- Reuse canonical command policies, RBAC, confirmation and Human Inbox.
- Require idempotency and broker/readback preconditions where applicable.
- Return receipts and refetch canonical state; no optimistic success.
- Test unauthorized, stale, duplicate, conflict and partial-failure behavior.
- Feature-flag live-capital actions and merge scoped PRs with security review.
