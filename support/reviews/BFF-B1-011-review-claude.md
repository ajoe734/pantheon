# Review: BFF-B1-011 — POST /bff/v5/interventions/{id}/decide

Reviewer: Claude
Date: 2026-05-23
PR: #437 (merge commit 6a48e3cc)
Owner: Codex2

## Summary

The task splits the previously shared `V5InterventionAction` generic handler for `/decide` into a dedicated `DecideV5Intervention` command endpoint. The implementation satisfies all spec acceptance criteria.

## Verification

```
python3 -m pytest services/control-plane/bff/test_v5_interventions.py services/control-plane/bff/test_final_command_execution_bridge.py -q
38 passed in 8.51s

python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/models.py services/control-plane/bff/action_catalog.py
OK (no output)
```

## Acceptance Criteria Check

| # | Criterion | Result |
|---|---|---|
| 1 | HTTP 202 with `data.command = "DecideV5Intervention"` | PASS |
| 2 | Command-store record: target `SentinelIntervention:{id}`, params include `intervention_id`, `interventionId`, `decision`, `audit_event` | PASS |
| 3 | `Idempotency-Key`, trace, correlation, request headers persisted in foundation metadata | PASS |
| 4 | Idempotency replay returns same commandId with `replayed: true` | PASS |
| 5 | Invalid decision returns 422 without store write | PASS |
| 6 | Body-level idempotency key rejected before store write | PASS |
| 7 | `DecideV5Intervention` in action catalog and `CommandType` enum | PASS |

## Scope Boundary

- claim/escalate/release/two-man-sign remain on the generic `V5InterventionAction` receipt path — not changed.
- `RemediateSentinelIntervention` precondition gates not touched.
- No live capital side effects introduced.
- Bridge test updated correctly: `/decide` route now emits `DecideV5Intervention`.

## Decision

**Approved.** Implementation is clean, well-scoped, all 38 tests pass, and spec section B4 is properly documented. Return to Codex2 for finalization.
