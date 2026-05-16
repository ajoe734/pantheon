# DEP-002-RB Review

Reviewer: Claude2
Task: DEP-002-RB — DeploymentPlan stage planner (rebaseline)
Owner: Codex
Date: 2026-05-16

## Verdict: APPROVED

## Scope Verified

- `POST /api/deployment/stage-planner/check` — new read-only route
- `StagePlannerCheckRequest` / `StagePlannerCheckResponse` models in `models.py`
- `DeploymentPlannerService.check_stage_transition()` in `service.py`
- `services/deployment/test_dep002_rebaseline_stage_planner.py` — 9 focused tests
- `services/deployment/contract.md` — route documented
- `services/deployment/README.md` — capability listed
- `support/evidence/DEP-002-RB/README.md` — evidence present

## Findings

**Read-only**: `check_stage_transition()` calls only `StagePlanner` methods and builds a
transient sentinel `DeploymentPlan` for validation. No store is written.

**Transition semantics**: The service correctly delegates to the canonical
`StagePlanner.derive_transition_type()`, `default_runtime_action()`, and
`default_scale()` in `services/control-plane/governance/deployment_plan.py`.
Forbidden transitions (paper→live skip, same-stage no-op) raise `DeploymentPlanError`
inside the try/except block, leaving `transition_type=None` and adding the message to
`errors[]`.

**rollback_required flag**: Correctly `True` for paper/canary/live targets,
`False` for frozen (set before the try/except so it is always present).

**Rollback sentinel**: When `rollback_action` is provided, a sentinel `RollbackRef` with
`target_artifact_id="stage-check-rollback-artifact"` and `target_version="0.9.0"` is
built. Because these differ from the sentinel plan's `artifact_id="stage-check-artifact"`
and `artifact_version="1.0.0"`, the rollback identity validation inside
`DeploymentPlan.validate()` passes and scale/stage cap checks are reached correctly.

**Missing rollback handling**: When `rollback_action` is omitted for a target that
requires rollback (paper/canary/live), `rollback=None` flows into the sentinel plan's
`validate()` which appends "rollback is required for target_stage '...'". The test
`test_stage_planner_check_rejects_missing_active_stage_rollback` verifies this path
for none→paper.

**Scale cap enforcement**: When `scale` is provided, `effective_scale` takes the
request value while `default_scale` remains the canonical default. The sentinel plan
validates both, reporting violations such as "canary target_stage requires
0 < capital_scale_pct <= 5" correctly.

**Ruleset field**: `ruleset = "DEP-002-RB-stage-planner-v1"` is hardcoded in
`StagePlannerCheckResponse` and verified in every parametrized test case.

## Test Verification

```
python3 -m py_compile services/deployment/models.py services/deployment/service.py \
  services/deployment/test_dep002_rebaseline_stage_planner.py
# passed

python3 -m pytest -q services/deployment/test_dep002_rebaseline_stage_planner.py
# 9 passed

python3 -m pytest -q services/deployment/test_dep002_rebaseline_stage_planner.py \
  services/deployment/test_dep001_rebaseline_service.py services/deployment/test_service.py
# 36 passed
```

## Notes

No issues. The implementation is narrow, read-only, and well-tested.
The contract and README are updated with the new route.
Returning to Codex for finalization.
