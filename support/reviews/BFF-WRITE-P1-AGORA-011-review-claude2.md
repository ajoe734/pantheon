# Review: BFF-WRITE-P1-AGORA-011

**Reviewer:** Claude2  
**Task:** POST /bff/agora/feedback (new route — distinct from per-signal feedback)  
**Commit reviewed:** 89c4561b  
**Review date:** 2026-05-29  
**Decision:** APPROVED

## Summary

Implementation of `POST /bff/agora/feedback` as a new canonical bulk feedback write endpoint,
distinct from the existing per-signal `POST /bff/agora/signals/{signalId}/feedback` at line 19054.

## Verification

```
pytest -q services/control-plane/bff/test_bff_write_gap_2026_05_28.py
# 5 passed in 3.22s

python3 -m py_compile services/control-plane/bff/main.py \
  services/control-plane/bff/read_store.py \
  services/control-plane/bff/test_bff_write_gap_2026_05_28.py
# OK
```

## Acceptance criteria check

| Criterion | Status |
|---|---|
| New route `POST /bff/agora/feedback` exists, distinct from per-signal route | PASS |
| Role check (analyst/operator/reviewer/approver/admin) with 403 on miss | PASS |
| `signal_id` required — 422 on missing | PASS |
| `verdict` enum (`useful`, `noise`, `false_positive`) — 422 on invalid | PASS |
| Signal 404 guard before and after `create_agora_feedback` | PASS |
| Idempotency via `Idempotency-Key` / `X-Idempotency-Key`, replay returns cached | PASS |
| Dry-run (`X-Dry-Run`) returns 200 with `dryRun=true`, no audit/SSE side-effects | PASS |
| Audit event recorded (`agora.feedback.create`) | PASS |
| SSE published to `agora.signals:{signalId}` channel | PASS |
| `read_store.create_agora_feedback` persists feedback and updates `latestFeedbackId` | PASS |
| Focused write-gap tests cover happy path, replay, dry-run, bad verdict, unknown signal | PASS |
| Pre-existing `test_bff_agora_core_contract.py` failures documented, not introduced here | PASS |

## Notes

- The error message in `_require_agora_bulk_feedback_role` says "requires analyst role" but the
  `suggestion` field correctly enumerates all 5 roles — minor wording inaccuracy, not blocking.
- Response envelope uses both snake_case and camelCase keys for `data` fields, consistent with
  sibling Agora routes.
- Owned layer is correctly bounded: no change to per-signal route, command executor, or deploy lane.
