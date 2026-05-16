# ASK-005 Review - Codex R3

Task: ASK-005 - approval / ask SSE event publishing
Owner: Claude
Reviewer: Codex
Reviewed at: 2026-05-16
Disposition: changes requested

## Scope Reviewed

- Fix commit `f5400502` (`ASK-005: move body idempotency validation before SSE publish (Codex R2)`)
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_ask005_sse_event_publishing_contract.py`
- `support/evidence/ASK-005/README.md`

## Resolved From R2

- Body idempotency validation now runs before approval SSE publication.
- The new regression test confirms a forbidden body `idempotencyKey` returns 400 with zero approval SSE events.
- Focused ASK-005 contract tests pass.

## Blocking Finding

1. Durable idempotency replay can still double-publish approval SSE events after in-memory idempotency state is cleared.

   `bff_approvals_decide` only checks `_FINAL_CONTRACT_IDEMPOTENCY` before publishing. If `_FINAL_CONTRACT_IDEMPOTENCY` is empty but `command_store` still contains the matching final-contract idempotency record, `_sem_command_response` correctly returns a replay from `command_store`, but the approval SSE event has already been published.

   Relevant flow:

   - approval pre-check reads `_FINAL_CONTRACT_IDEMPOTENCY` at `services/control-plane/bff/main.py:25908`
   - approval event publish happens at `services/control-plane/bff/main.py:25911`
   - `_sem_command_response` performs durable replay lookup via `command_store.get_command_by_idempotency_key(...)` at `services/control-plane/bff/main.py:23892`

   Reviewer probe:

   ```text
   first 202 replay 202
   replayed True
   buffer lengths 1 2
   event types ['approval.decided', 'approval.decided']
   records 1
   ```

   Required before approval: make the approval SSE publish guard match `_sem_command_response` replay semantics, including the durable `command_store` existing-record path. Add coverage that clears `_FINAL_CONTRACT_IDEMPOTENCY` after the first approval decision, retries with the same `Idempotency-Key`, receives `replayed=True`, and still has exactly one approval SSE event.

## Verification

Reviewer commands:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -q
# 10 passed in 13.37s

git diff --check -- services/control-plane/bff/main.py \
  services/control-plane/bff/test_ask005_sse_event_publishing_contract.py \
  support/evidence/ASK-005/README.md
# passed
```

The R2 validation-order fix passes, but the durable replay side-effect path above still blocks approval.

## Decision

Changes requested. Return ASK-005 to Claude for a targeted durable-replay SSE de-duplication patch and regression test.
