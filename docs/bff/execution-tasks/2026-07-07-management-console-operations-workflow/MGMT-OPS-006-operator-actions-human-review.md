# MGMT-OPS-006 - Governed Operator Actions And Human Review

Owner: Antigravity

Reviewer: Claude2

Wave: 2

Dependencies:

- `MGMT-OPS-003`
- `MGMT-OPS-004`
- `MGMT-OPS-005`

Source plan:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`

## Goal

Wire management-console actions through Human Review and auditable receipts so
operators can monitor and act without bypassing governance.

## Required Work

- Define allowed row actions for observe, request review, pause paper runtime,
  resume paper runtime, demote/retire, promote candidate, rebalance proposal,
  approved apply, and emergency containment.
- Enforce preconditions: persona identity, stage, runtime/ledger/capital binding,
  source confidence, policy gate, and human-review requirement.
- Add review packet creation from Persona Fleet, Performance Attribution,
  Portfolio Book incidents, Persona League, and Quarterly Ranking.
- Ensure emergency containment can reduce or pause risk, but cannot promote or
  increase allocation.
- Add audit receipt display after approved apply commands.
- Add BFF and frontend tests for action gating, idempotency, rejected preconditions,
  and receipt linking.

## Acceptance

- Every mutating action is represented as request, approval, apply, and receipt.
- Ranking pages only create recommendations or review packets.
- Emergency containment cannot create promotion or allocation-increase side
  effects.
- Persona Fleet and Portfolio Book show the latest review/action state and link
  to evidence.
- Tests prove unauthorized or under-evidenced actions are blocked.

## Artifacts

- `services/control-plane/bff`
- `execute-plans:src/management/pages`
- `execute-plans:src/lib`
- `execute-plans:e2e`
