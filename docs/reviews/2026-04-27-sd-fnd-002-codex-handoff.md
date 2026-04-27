# SD-FND-002 Codex Handoff

Task: `SD-FND-002`
Owner: Codex
Reviewer: Claude (auto-reassigned back to Claude on 2026-04-27 after Codex2 worker terminal)
Status: ready for review

## Scope

Adopted the shared foundation envelope in one BFF command path and one
runtime-manager action path.

The BFF pilot path is `POST /api/v1/operator/commands`. The runtime-manager
pilot path is `RuntimeManagerService.execute_kill_switch`, surfaced through the
existing kill-switch dispatch flow.

## Implemented Adoption

- BFF command admission now builds a shared `TraceContext`, `CommandEnvelope`,
  `IdempotencyRecord`, `PolicyDecision`, and `AuditAction`.
- BFF command records persist a serialized foundation context both at the
  command record root and under the audit record, so command status reads can
  replay the trace/audit boundary.
- BFF accepts `X-Trace-Id`, `X-Correlation-Id`, `X-Request-Id`, and
  `X-Idempotency-Key` for the pilot command path.
- Duplicate BFF submissions with the same idempotency key and payload replay the
  original receipt instead of creating a second command.
- BFF policy denials and validation failures now include stable shared
  `ErrorEnvelope` payloads, plus policy/audit context where applicable.
- Runtime-manager kill-switch dispatch now builds and returns foundation
  context, propagates upstream trace/correlation ids into command metadata, and
  stores an idempotency ledger alongside kill-switch state.
- Runtime-manager duplicate kill-switch requests with the same idempotency key
  and request hash replay the first dispatch result without adding another
  kill-switch audit entry.

## Acceptance Evidence

- Trace propagation:
  - BFF preserves caller trace/correlation headers in the stored foundation
    context.
  - Runtime-manager preserves upstream foundation trace/correlation ids and
    mirrors them into the emitted kill-switch command metadata.
- Idempotency:
  - BFF duplicate command submission returns the same receipt and leaves one
    command record.
  - Runtime-manager duplicate kill-switch dispatch returns
    `idempotent_replay=true` and leaves one kill-switch audit entry.
- Audit emission:
  - BFF stores foundation `AuditAction` in command/audit context.
  - Runtime-manager returns foundation `AuditAction` and keeps the existing
    kill-switch audit entry.
- Policy denial:
  - BFF role-denied command submission returns a shared policy-denial
    `ErrorEnvelope` with `PolicyDecision(decision=deny)`.
- Stable validation error:
  - BFF invalid command params return a shared validation `ErrorEnvelope`.

## Verification

```text
pytest services/control-plane/bff/test_governance_command_submission.py -q
..........                                                               [100%]
10 passed in 3.05s

pytest services/runtime-manager/test_runtime_manager.py -q
.................................                                  [100%]
39 passed in 1.69s

pytest services/foundation/tests -q
..........                                                               [100%]
10 passed in 0.18s

2026-04-27 owner refresh after review reassignment to Codex2:
pytest services/control-plane/bff/test_governance_command_submission.py services/runtime-manager/test_runtime_manager.py services/foundation/tests -q
...........................................................              [100%]
59 passed in 3.09s

2026-04-27 owner refresh after review reassignment back to Claude:
pytest services/control-plane/bff/test_governance_command_submission.py services/runtime-manager/test_runtime_manager.py services/foundation/tests -q
...........................................................              [100%]
59 passed in 3.08s
```

## Files

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/test_governance_command_submission.py`
- `services/runtime-manager/service.py`
- `services/runtime-manager/test_runtime_manager.py`

## Deferred

- This task does not add database-backed idempotency storage for all services.
- This task does not promote any EP5 live/canary proof or broker side effect.
- This task does not claim full BFF/runtime-manager coverage; it proves one
  pilot command path and one pilot runtime-manager action path.
