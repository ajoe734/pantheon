# ASK-005 Evidence: approval / ask SSE event publishing

**Task:** ASK-005  
**Owner:** Claude  
**Reviewer:** Codex  
**Phase:** Sprint 5 / EPIC-RESEARCH

## Scope

Implement SSE event publishing for the approval and ask channels when BFF mutations occur.

## Changes

### services/control-plane/bff/main.py

1. `sem_agora_ask_create_session` (`POST /bff/agora/ask/sessions`):
   - Added `_publish_event` call publishing `ask.session.started` to the `ask` channel after session creation.
   - Idempotency replay is blocked by the early-return guard (`_agora_core_idempotency_check`) before the publish call.

2. `bff_approvals_decide` (`POST /bff/approvals/{id}/decide`):
   - Added `_APPROVAL_STAGE_CHANGE_DECISIONS` frozenset (`request_revision`, `escalate`, `freeze`) to classify stage-change decisions.
   - Added idempotency pre-check before SSE publish: resolves key, computes request hash matching `_sem_command_response`, checks `_FINAL_CONTRACT_IDEMPOTENCY`; skips SSE publish on confirmed replay.
   - Added `_publish_event` calls publishing to the `approval` channel.
   - `approve` / `reject` → `approval.decided` with outcome `approved` / `rejected`.
   - `request_revision` / `escalate` / `freeze` → `approval.stage.changed` with `current_stage=raw_decision`.
   - Event is not published on role-gate failures (403 is raised before the publish code).
   - Event is not published on in-memory idempotency replay (`_FINAL_CONTRACT_IDEMPOTENCY` pre-check skips publish).
   - Event is not published on durable `command_store` replay either: extended pre-check now also queries `command_store.get_command_by_idempotency_key` so that `_FINAL_CONTRACT_IDEMPOTENCY` eviction cannot cause a second SSE publish. *(Codex R3 fix)*
   - Event is not published on idempotency conflict (same key, different payload): `_is_idem_conflict` flag mirrors the `_sem_command_response` conflict branch — when `_FINAL_CONTRACT_IDEMPOTENCY` or `command_store` holds a different hash for the same key, SSE publish is skipped before the 409 is raised. *(Codex R4 fix)*

### services/control-plane/bff/test_ask005_sse_event_publishing_contract.py

10 contract tests covering:
- `test_create_ask_session_publishes_ask_session_started`
- `test_create_ask_session_idempotency_replay_does_not_double_publish`
- `test_bff_approvals_decide_approve_publishes_approval_decided`
- `test_bff_approvals_decide_reject_publishes_approval_decided_rejected`
- `test_bff_approvals_decide_request_revision_publishes_stage_changed`
- `test_bff_approvals_decide_escalate_publishes_stage_changed` *(new — Codex R1 fix)*
- `test_bff_approvals_decide_freeze_publishes_stage_changed` *(new — Codex R1 fix)*
- `test_bff_approvals_decide_replay_does_not_double_publish` *(new — Codex R1 fix)*
- `test_bff_approvals_decide_body_idempotency_key_rejected_does_not_publish` *(new — Codex R2 fix)*
- `test_bff_approvals_decide_durable_replay_does_not_double_publish` *(new — Codex R3 fix)*
- `test_bff_approvals_decide_idempotency_conflict_does_not_double_publish` *(new — Codex R4 fix)*
- `test_bff_approvals_decide_role_gate_failure_does_not_publish`

## Codex Review Fixes (commit after afaca235)

Two blocking findings addressed:

1. **escalate/freeze event semantics**: Both now correctly publish `approval.stage.changed` (not `approval.decided`). Fix: `_APPROVAL_STAGE_CHANGE_DECISIONS` frozenset; SSE branch now uses `raw_decision` membership test instead of `command_type` alone.

2. **Approval idempotency replay de-duplication**: SSE publish is now guarded by an in-memory idempotency pre-check. The same hash computed by `_sem_command_response` is checked against `_FINAL_CONTRACT_IDEMPOTENCY` before any publish; replay calls are skipped.

## Codex R2 Fix

1. **Body idempotency key rejected before SSE publish**: `_reject_body_idempotency_key(payload)` is now called in `bff_approvals_decide` before the idempotency pre-check and SSE publish block. A request with a valid `Idempotency-Key` header but a forbidden body `idempotencyKey` now returns 400 without ever writing to the approval SSE buffer.

## Codex R3 Fix

1. **Durable command_store replay de-duplication**: After the `_FINAL_CONTRACT_IDEMPOTENCY` in-memory check, the pre-check now also calls `command_store.get_command_by_idempotency_key(_idem_key)` to detect durable replay records. If the stored `request_hash` matches the computed hash, `_is_approval_replay` is set to `True` and the SSE publish is skipped — preventing double-publish when in-memory state is evicted but the durable store still holds the command record. This matches the exact replay semantics of `_sem_command_response`.

## Codex R4 Fix

1. **Idempotency conflict pre-check (no SSE on 409 path)**: Introduced `_is_idem_conflict` flag that mirrors the conflict branch of `_sem_command_response`. When `_FINAL_CONTRACT_IDEMPOTENCY` holds the same key but a different `request_hash`, `_is_idem_conflict` is set and the SSE publish block is skipped before control reaches `_sem_command_response` (which raises 409). The durable store branch is similarly extended: if `command_store` holds the key with a non-matching hash, `_is_idem_conflict` is also set. Result: re-using an idempotency key with a different payload returns 409 with zero additional SSE events.

## Verification

```
pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -v
# 12 passed in 9.45s

pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py \
       services/control-plane/bff/test_bff_approvals_decide_contract.py \
       services/control-plane/bff/test_pkt006_approval_queue_contract.py \
       services/control-plane/bff/test_cw01_consult_request_contract.py \
       services/control-plane/bff/test_cw02_debate_transcript_contract.py \
       services/control-plane/bff/test_cw03_committee_board_contract.py \
       services/control-plane/bff/test_cw04_redteam_memo_contract.py -q
# 66 passed in 106.47s

pytest services/control-plane/bff/test_final_contract_primitives.py \
       services/control-plane/bff/tests/test_command_replay_conflict.py \
       services/control-plane/bff/test_governance_command_submission.py -q
# 33 passed in 39.74s
```

Total: 111 adjacent tests + 12 ASK-005 tests = 123 passing, 0 failures.
