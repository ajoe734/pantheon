# Review: P0-STATE-001 — Add artifact/deployment/runtime invariant tests

**Reviewer:** Claude  
**Owner:** Codex2  
**Date:** 2026-05-01  
**Status:** APPROVED

---

## Verification Runs

All test suites pass green:

```
pytest services/registry/test_service.py services/deployment/test_service.py -q
→ 56 passed in 22.31s

cd services/control-plane/governance && python3 -m unittest discover -s . -p 'test_*.py'
→ Ran 101 tests in 0.045s — OK

cd services/runtime-manager && python3 -m unittest test_runtime_manager
→ Ran 46 tests in 1.055s — OK

Total: 203 tests, 0 failures
```

---

## Acceptance Criteria Evaluation

### 1. paper/live/canary are not accepted artifact_state values ✅

Covered at three layers:

- **Registry model** (`test_artifact_state_rejects_deployment_stage_values`): Pydantic validation rejects `paper`, `canary`, `live` as `ArtifactState` on `RegistryEntryCreate`.
- **Registry FastAPI endpoint** (`test_register_rejects_deployment_stage_as_artifact_state`): Returns HTTP 422 when those values are passed.
- **Deployment service** (`test_create_plan_rejects_deployment_stage_as_artifact_state`): Returns 422 with `"requires artifact_state=approved"` message.
- **Governance layer** (`test_deployment_stage_values_are_not_artifact_state`): `StagePlanner.create_plan` raises `DeploymentPlanError` with the same message.

### 2. Deployment requires approved artifact, DeploymentPlan, RuntimeBinding, and matching stage ✅

- **Approved artifact required:**
  - `test_requires_approved_artifact_state` and `test_deployment_plan_requires_approved_artifact_and_matching_approval` (governance)
  - `test_deployment_stage_requires_approved_artifact` and `test_update_deployment_summary_unapproved_returns_400` (registry)
- **DeploymentPlan required:**
  - `test_deploy_requires_deployment_plan_reference` (runtime-manager) — empty `plan_id` raises `RuntimeManagerError`.
- **RuntimeBinding with matching stage:**
  - `test_runtime_binding_stage_matches_deployment_plan_target` (runtime-manager) — binding `deployment_mode` equals plan `target_stage`.
- **Forbidden stage skips blocked:**
  - `test_validate_rejects_skipped_stage_transition` (deployment service) — `paper → live` skip rejected.
  - `test_paper_to_live_skip_is_forbidden` (governance) — same invariant at planner level.

---

## SA-12 Section 16 Invariant Coverage

| Required test | Present |
|---|---|
| `test_artifact_state_does_not_include_paper_or_live` | ✅ (multiple layers) |
| `test_deployment_stage_requires_approved_artifact` | ✅ |
| `test_runtime_binding_requires_deployment_plan` | ✅ (`test_deploy_requires_deployment_plan_reference`) |
| `test_runtime_binding_stage_matches_deployment_plan_target` | ✅ (explicitly named) |

---

## Overall Assessment

The implementation correctly separates `ArtifactState` (draft/candidate/approved/retired) from `DeploymentStage` (none/paper/canary/live/frozen) at all service layers. Cross-state invariants are enforced with appropriate HTTP status codes (404/400/422) and meaningful error messages. No gaps in coverage relative to the task acceptance criteria or SA-12 required tests.

**Decision: APPROVE — return to Codex2 for finalization.**
