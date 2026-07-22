# AG-WS-OPS-002 — Governed Workshop research, consultation, and conclusion operations

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex2
Reviewer: Codex
Depends on: `AG-WS-OPS-001`

## Objective

Implement the remaining operations formally deferred by `AG-GAP-005`:

- `POST /bff/agora/workshops/{workshop_id}/research-runs`
- `POST /bff/agora/workshops/{workshop_id}/consultations`
- `POST /bff/agora/workshops/{workshop_id}/conclude`

## Owned scope

- Workshop route/service integration with research and consultation
- conclusion state machine and final-version binding
- additive contract/OpenAPI and focused integration tests

## Required work

Research and consultation requests must bind to an existing workshop version,
use the governed downstream service rather than a fake result, persist their
lineage and lifecycle state, and be idempotent. Conclusion must validate a
complete selected final version and atomically move the workshop to its
terminal state while emitting an audit event. Partial downstream failure must
leave a resumable, truthful state.

## Acceptance

- All three routes return non-501 typed responses for valid requests.
- Downstream IDs and status are authoritative and survive BFF restart.
- Duplicate requests do not create duplicate runs/consultations/conclusions.
- Conclusion refuses missing/incomplete/unselected versions and stale ETags.
- Cross-tenant, role, timeout, retry, and partial-failure tests pass.
- No response fabricates a completed research or consultation result.
- PR merges to `dev` with exact contract handoff for compatibility tasks.

## Exclusions

- No frontend changes.
- No direct mutation of downstream read stores.
- No automatic capital allocation, deployment, or order routing.
