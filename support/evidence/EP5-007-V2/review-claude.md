# EP5-007-V2 Review: Rollback Drill Harness

Reviewer: Claude
Date: 2026-05-20
Status: approved

## Scope Reviewed

- `services/governance/ep5_proof/rollback_drill_harness.py`
- `docs/operations/rollback_drill_runbook.md`
- `tests/governance/test_rollback_drill_harness.py`

## Verification

Ran focused tests locally:

```
python3 -m pytest tests/governance/test_rollback_drill_harness.py -q
5 passed in 1.38s
```

PR #298 (merge commit 3d65f751) passed all GitHub CI checks: Commit trailers,
Runtime mirror guard, Smoke acceptance, Orchestrator Sync.

## Review Findings

### rollback_drill_harness.py

- `dry_run: Literal[True] = True` and `side_effects_allowed: Literal[False] = False` are
  structurally enforced at the Pydantic model level — impossible to relax from external input.
- `mode` and `order_route_mode` constrained to `Literal["validate_only", "sandbox"]`, blocking
  any "live" routing path at the schema layer.
- `_runtime_store` context manager correctly handles both ephemeral temp dirs and
  explicit store paths. Cleanup is guaranteed.
- `_validate_runtime_rollback_response` validates all critical lineage invariants:
  old binding retired, replacement binding carries `rollback_parent`, `rollback_action_type`,
  and `opened_by_artifact_id` preserved.
- Post-proof validation checks `rollback_drill_completed` and `live_capital_side_effects`
  explicitly — fails closed if either is wrong.
- Dynamic module loading for RuntimeManagerService uses module-name caching to avoid
  repeated `exec_module` calls.

### rollback_drill_runbook.md

Comprehensive. Covers command example, all expected output fields, failure-blocking
criteria (broker dry-run blocking reasons, un-retired binding, missing rollback_parent,
live_capital_side_effects=true), and local verification command.

### test_rollback_drill_harness.py

5 tests covering:
1. Full happy path — all output fields and proof packet asserted
2. `liquidate_then_replace` with paused replacement
3. Empty `broker_subaccount_ref` rejected before drill execution
4. `order_route_mode="live"` rejected at schema validation
5. CLI JSON output written correctly

Coverage is adequate for the task scope.

## Acceptance Criteria Check

- Harness produces `rollback_drill_completed` in EP5ProofPacket: **passed**
- `live_capital_side_effects = false` enforced in harness output and proof packet: **passed**
- Runbook documents expected output and failure handling: **passed**
- Tests pass `pytest -q exit 0`: **passed** (5/5)
- No live broker side effects: **passed** (enforced at type level)

## Decision

Approved. No required changes. Return to Codex2 for closeout finalization.
