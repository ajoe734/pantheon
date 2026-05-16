# ASK-005 Evidence: approval / ask SSE event publishing

**Task:** ASK-005  
**Owner:** Claude  
**Reviewer:** Codex2  
**Phase:** Sprint 5 / EPIC-RESEARCH

## Scope

Implement SSE event publishing for the approval and ask channels when BFF mutations occur.

## Changes

### services/control-plane/bff/main.py

1. `sem_agora_ask_create_session` (`POST /bff/agora/ask/sessions`):
   - Added `_publish_event` call publishing `ask.session.started` to the `ask` channel after session creation.
   - Idempotency replay is blocked by the early-return guard (`_agora_core_idempotency_check`) before the publish call.

2. `bff_approvals_decide` (`POST /bff/approvals/{id}/decide`):
   - Added `_publish_event` calls publishing to the `approval` channel.
   - `approve` / `reject` → `approval.decided` with outcome `approved` / `rejected`.
   - `request_revision` / `escalate` / `freeze` → `approval.stage.changed` with `current_stage=raw_decision`.
   - Event is not published on role-gate failures (403 is raised before the publish code).

### services/control-plane/bff/test_ask005_sse_event_publishing_contract.py (new)

6 contract tests covering:
- `test_create_ask_session_publishes_ask_session_started`
- `test_create_ask_session_idempotency_replay_does_not_double_publish`
- `test_bff_approvals_decide_approve_publishes_approval_decided`
- `test_bff_approvals_decide_reject_publishes_approval_decided_rejected`
- `test_bff_approvals_decide_request_revision_publishes_stage_changed`
- `test_bff_approvals_decide_role_gate_failure_does_not_publish`

## Verification

```
pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -v
# 6 passed

pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q
# 14 passed

pytest services/control-plane/bff/test_bff_approvals_decide_contract.py \
       services/control-plane/bff/test_ask_001_sessions_contract.py \
       services/control-plane/bff/test_ask_003_committee_lifecycle.py \
       services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 113 passed
```

Total: 133 tests passing, 0 failures.
