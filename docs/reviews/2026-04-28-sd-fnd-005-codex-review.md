# SD-FND-005 Review

Task: `SD-FND-005`
Owner: Codex2
Reviewer: Codex
Disposition: **Approved**
Date: 2026-04-28

## Findings

No blocking findings remain.

The previously rejected optional-binding REPLACE replay window is now covered:
durable command replay restores omitted optional fields before rebuilding the
command, and REPLACE replay can recover the old binding from an existing
`ks-replace-{command_id}` replacement via `rollback_parent` before falling back
to active-binding lookup.

## Scope Verified

- `services/foundation/command_recovery.py` adds shared command-recovery ledger
  serialization, idempotency entry validation, and quarantine audit events.
- `services/runtime-manager/service.py` now persists reserved, executing, and
  succeeded kill-switch idempotency records; resumes executing records through
  the durable command path; records recovery/quarantine audit; and reuses an
  existing REPLACE fallback binding on replay instead of creating a duplicate.
- `services/runtime-manager/test_runtime_manager.py` covers PAUSE crash replay,
  explicit-binding REPLACE replay, optional-binding REPLACE replay, corrupt
  snapshot quarantine, and corrupt foundation idempotency entry quarantine.
- `services/foundation/tests/test_primitives.py` covers command-recovery entry
  validation and corrupt partial-state quarantine.

## Acceptance Mapping

- Crash replay does not duplicate side effects: verified by the PAUSE replay
  regression and both REPLACE replay regressions.
- Partial durable state recovers or quarantines with audit: verified by
  recovery audit assertions and corrupt-entry quarantine tests.
- Shared foundation replay primitives are used: runtime-manager stores and
  loads kill-switch idempotency through `command_recovery_entry`,
  `idempotency_record_from_entry`, and `load_command_recovery_entries`.

## Verification Run

```text
pytest services/control-plane/bff/test_governance_command_submission.py services/runtime-manager/test_runtime_manager.py services/foundation/tests -q
=> 64 passed in 2.89s
```
