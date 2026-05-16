# ASK-005 Review - Codex R2

Task: ASK-005 - approval / ask SSE event publishing
Owner: Claude
Reviewer: Codex
Reviewed at: 2026-05-16
Disposition: changes requested

## Scope Reviewed

- Fix commit `73304fe0` (`ASK-005: fix escalate/freeze SSE semantics and approval replay de-dup`)
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_ask005_sse_event_publishing_contract.py`
- `support/evidence/ASK-005/README.md`

## Resolved From R1

- `escalate` and `freeze` now publish `approval.stage.changed` instead of `approval.decided`.
- Approval idempotency replay no longer publishes a duplicate SSE event for the same request hash.
- Added direct tests for `escalate`, `freeze`, and approval replay de-duplication.

## Blocking Finding

1. Invalid final-contract request bodies can still publish an approval SSE event before returning 400.

   In `services/control-plane/bff/main.py`, `bff_approvals_decide` publishes the approval SSE event before `_sem_command_response` validates the final-contract body with `_reject_body_idempotency_key`. A request with a valid `Idempotency-Key` header but a forbidden body `idempotencyKey` returns `400 INVALID_REQUEST`, yet the approval SSE buffer already contains `approval.decided`.

   Relevant flow:

   - pre-publish idempotency check runs at `services/control-plane/bff/main.py:25898`
   - event publish runs at `services/control-plane/bff/main.py:25924`
   - `_sem_command_response(...)` is called only afterward at `services/control-plane/bff/main.py:25931`
   - `_sem_command_response` rejects body idempotency keys before command admission

   Reviewer probe:

   ```text
   status: 400
   response code: INVALID_REQUEST / body_idempotency_key
   approval buffer length: 1
   event types: ['approval.decided']
   ```

   Required before approval: move the body idempotency validation, or equivalent final-contract request validation, before any approval SSE publish. Add direct ASK-005 coverage asserting the body-idempotency rejection does not publish to the approval channel. Keep the existing replay de-dup coverage.

## Verification

Reviewer commands:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -q
# 9 passed in 12.21s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/test_bff_approvals_decide_contract.py \
  services/control-plane/bff/test_ask_001_sessions_contract.py \
  services/control-plane/bff/test_ask_003_committee_lifecycle.py \
  services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 99 passed in 90.16s

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q
# 14 passed in 12.49s
```

The claimed R1 fixes pass, but the invalid-request SSE side effect above blocks approval.

## Decision

Changes requested. Return ASK-005 to Claude for a targeted validation-order patch and a new regression test.
