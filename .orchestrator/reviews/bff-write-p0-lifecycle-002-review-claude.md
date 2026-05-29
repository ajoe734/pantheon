# Review: BFF-WRITE-P0-LIFECYCLE-002

**Task:** POST /bff/capital-pools/{id}/actions/ApprovePool (register in action_catalog)
**Reviewer:** Claude
**Owner:** Claude2
**Date:** 2026-05-29
**Commit reviewed:** 835970c0

## Verdict: APPROVED

## Scope checked

Five files changed in commit 835970c0:

1. `services/control-plane/bff/action_catalog.py` — ApprovePool entry registered
2. `services/control-plane/bff/models.py` — CommandType.APPROVE_POOL / ADVANCE_LIFECYCLE / START_RUNTIME added
3. `services/control-plane/bff/command_executor.py` — `_execute_approve_pool` handler + `_EXECUTORS` dispatch entry
4. `services/control-plane/bff/main.py` — capital-pool action route: bypass deprecated gate for registered actions
5. `services/control-plane/bff/test_bff_write_gap_2026_05_28.py` — 8 new tests

## Spec compliance (Card P0-2)

| Requirement | Implemented |
|---|---|
| `action_id="ApprovePool"` | ✓ `action_catalog.py` |
| `entity_type="CapitalPool"` | ✓ |
| `required_roles=["treasury_approver"]` | ✓ |
| `idempotency_required=True` | ✓ |
| `risk_level=HIGH` | ✓ |
| `requires_approval=True` | ✓ |
| Body: `memo ≥8 chars` (validated) | ✓ `_execute_approve_pool` raises ValueError |
| Body: `confirm_token` optional | ✓ forwarded to internal API |
| State: `draft → approved` (one-way) | ✓ description notes "one-way" |
| Route no longer returns 410 for ApprovePool | ✓ `_CAPITAL_POOL_REGISTERED_ACTIONS` bypass |
| CommandType.APPROVE_POOL enum | ✓ `models.py` |
| Internal POST to `/api/internal/v1/capital-pools/{pool_id}/approve` | ✓ |

## Test verification

```
pytest services/control-plane/bff/test_bff_write_gap_2026_05_28.py -v
8 passed in 0.97s
```

All 8 tests cover: catalog registration, entry shape, pool_id validation, memo length validation, valid happy path, confirm_token forwarding, CommandType enum existence, route registration.

## Notes

- `ADVANCE_LIFECYCLE` and `START_RUNTIME` enum values are added as stubs in `models.py` — correctly documented as "not changing" in the commit (sibling tasks P0-1, P0-3 will wire these).
- `_CAPITAL_POOL_REGISTERED_ACTIONS` defined inside the route function — minor style issue, no correctness impact.
- The route still calls `_require_read_role(identity)` as a baseline auth check; actual treasury_approver role enforcement is downstream through the action catalog machinery — consistent with existing patterns.
- Commit trailers correctly include `LLM-Agent: Claude2`, `Task-ID: BFF-WRITE-P0-LIFECYCLE-002`, `Reviewer: Claude`, `Verified: pytest ... → 8 passed`.

## Outcome

Implementation is spec-compliant, test-covered, and production-quality. Approving.
