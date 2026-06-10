# BFF-B1-010 Review — Claude

**Task:** POST /bff/approvals/{id}/decide and batch-decide
**Owner:** Codex
**Reviewer:** Claude
**Date:** 2026-05-23
**Status:** Approved

## Scope

Reviewed the two implementation commits merged via PR #434:

- `974e269d` — anchor commit: batch-decide route skeleton
- `08f26085` — final implementation: request_changes alias, batch-decide per-item routing, contract tests, spec entry

Files changed:
- `services/control-plane/bff/main.py` (+199 lines net)
- `services/control-plane/bff/test_bff_approvals_decide_contract.py` (new, 126 lines)
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md` (§13.5 added)

## Verification

Ran both test suites referenced in the commit Verified trailer:

```
pytest services/control-plane/bff/test_bff_approvals_decide_contract.py -q
# → 19 passed in 4.47s

pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -q
# → 12 passed in 4.05s
```

No regressions in the SSE publishing suite.

## Acceptance Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Single decide accepts approve/reject/request_revision/request_changes/escalate/freeze and records command-store receipts | Pass |
| 2 | Approver/admin role gate, typed validation failures, header idempotency replay preserved | Pass |
| 3 | Batch-decide accepts bounded decision list and returns per-id result status | Pass |
| 4 | Batch-decide rejects body idempotency keys before writing commands | Pass |
| 5 | Accepted batch items recorded through shared command store; failures isolated per item | Pass |

## Code Quality Notes

- `request_changes` alias correctly maps to `CommandType.REQUEST_APPROVAL_REVISION` and is registered in `_APPROVAL_STAGE_CHANGE_DECISIONS` (emits `approval.stage.changed`, not `approval.decided`).
- `_reject_body_idempotency_key(payload)` is called before `_resolve_final_idempotency_key` in batch route — correct ordering prevents any side effects on invalid requests.
- `_BFF_APPROVAL_DECIDE_VALUES` constant keeps error messages in sync across single and batch routes.
- Scope is narrow: no unrelated routes, auth/session logic, or frontend contracts touched.

## Decision

**Approved.** All acceptance criteria satisfied; test suite clean; implementation boundary matches task scope.
