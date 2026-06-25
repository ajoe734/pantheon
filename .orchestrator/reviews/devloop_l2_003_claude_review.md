# Review: DEVLOOP-L2-003 — Fix deployment-plan.current_stage consistency

Reviewer: Claude
Date: 2026-06-14
PR: #1560 (merge fbac2006, task commit 643f4c9e)

## Verdict: APPROVED

## Changes Reviewed

### `services/deployment/service.py`
- `record_binding_created` now calls `_mark_plan_binding_created` after recording the saga event: sets plan status to `EXECUTING` and writes `binding_id`/`runtime_id` into `metadata.runtime_lifecycle`.
- `record_runtime_active` now calls `_mark_plan_runtime_active`: advances `plan.current_stage = DeploymentStage(saga.target_stage)`, sets status to `EXECUTED`, writes full lifecycle metadata.
- Target-stage equality guard correctly validates `plan.target_stage == saga.target_stage` before advancing.
- HTTP endpoints now catch `DeploymentPlanError` in addition to `DeploymentSagaError` — both raise 400 on plan inconsistency.
- `_mutable_plan_copy` uses round-trip dict to avoid aliased mutation.

### `services/control-plane/governance/deployment_plan.py`
- Validation rule relaxed: `current_stage == target_stage` is accepted when `plan_status == EXECUTED`. This is correct — terminal state of an executed plan has current_stage == target_stage.
- Transition-type check also skipped when stages already match (stages_match guard).

## Test Coverage
- `test_saga_progress_and_inbox_replay_receipts` extended: asserts plan state after binding_created (executing, current_stage=none) and after runtime_active (executed, current_stage=paper, projection current_stage=paper).
- `test_executed_plan_allows_current_stage_to_match_target_stage`: validates the relaxed constraint is accepted for EXECUTED plans and still rejected for EXECUTING plans.

## Verification
```
python3 -m pytest services/deployment/test_service.py  → 22 passed
cd services/control-plane/governance && python3 -m unittest test_deployment_plan.py → 29 passed
```

## Notes
- Fix is scoped correctly: no change to RuntimeManager, LEAN runtime, or projection read models.
- Commit trailers present and correct. PR merged into dev.
- Acceptance criteria met: binding → active transitions now advance `current_stage` to the correct deployment stage.
