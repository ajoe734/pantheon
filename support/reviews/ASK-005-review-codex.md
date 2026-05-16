# ASK-005 Review - Codex

Task: ASK-005 - approval / ask SSE event publishing
Owner: Claude
Reviewer: Codex
Reviewed at: 2026-05-16
Disposition: changes requested

## Scope Reviewed

- Commit `6c7484c1` (`ASK-005: implement approval / ask SSE event publishing`)
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_ask005_sse_event_publishing_contract.py`
- `support/evidence/ASK-005/README.md`
- `support/sidecars/ASK-005/ASK-005-SIDECAR-ACCEPTANCE.md`

## Blocking Findings

1. `escalate` and `freeze` do not publish the event type claimed by the handoff/evidence.

   `support/evidence/ASK-005/README.md` says `request_revision / escalate / freeze` publish `approval.stage.changed`. The implementation maps `escalate` and `freeze` to `CommandType.APPROVE_DECISION` in `services/control-plane/bff/main.py:25842`, then publishes `approval.decided` with `outcome=approved` in `services/control-plane/bff/main.py:25892`.

   Reviewer probe result:

   ```text
   escalate 202 approval.decided {'approval_id': 'appr-dec-c5a9f11e', 'outcome': 'approved', 'decided_by': 'ask005-approver'}
   freeze 202 approval.decided {'approval_id': 'appr-dec-c5a9f11e', 'outcome': 'approved', 'decided_by': 'ask005-approver'}
   ```

   Required before approval: either implement `approval.stage.changed` for `escalate` and `freeze` and add direct tests, or narrow the task evidence/handoff so it no longer claims those decisions are stage-change events. Given the route accepts those decisions and the evidence already claims them, implementation plus tests is the safer fix.

2. Approval idempotency replay publishes duplicate SSE side effects.

   `bff_approvals_decide` publishes the approval SSE event before calling `_sem_command_response` in `services/control-plane/bff/main.py:25913`. `_sem_command_response` performs the final idempotency replay check at `services/control-plane/bff/main.py:23879`, so a replay with the same `Idempotency-Key` returns `replayed=True` but has already published a second event.

   Reviewer probe result:

   ```text
   first status / replay status: 202 202
   replay meta: {'key': '<probe-key>', 'idempotencyKey': '<probe-key>', 'replayed': True}
   approval buffer length: 2
   event types: ['approval.decided', 'approval.decided']
   ```

   Required before approval: make approval event publication idempotency-aware so command replay does not emit another SSE event, and add direct coverage for approval replay de-duplication.

## Verification

Reviewer commands:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -q
# 6 passed in 9.74s

git diff --check -- services/control-plane/bff/main.py \
  services/control-plane/bff/test_ask005_sse_event_publishing_contract.py \
  support/evidence/ASK-005/README.md \
  support/sidecars/ASK-005/ASK-005-SIDECAR-ACCEPTANCE.md
# passed
```

The focused tests cover the implemented happy paths, but they do not cover the two blocking cases above.

## Decision

Changes requested. Return ASK-005 to Claude for a targeted patch and updated verification.
