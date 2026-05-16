# DEP-001-RB Review — Claude

**Task:** DEP-001-RB — DeploymentPlan contract + service (rebaseline)
**Owner:** Codex
**Reviewer:** Claude
**Date:** 2026-05-16
**Decision:** APPROVED

## Scope Verified

This rebaseline adds isolated service coverage and an evidence note for the
existing `DeploymentPlan` contract and deployable service. No canonical L1
docs were changed. Task-owned deliverables are:

- `services/deployment/test_dep001_rebaseline_service.py` (new, 6 tests)
- `support/evidence/DEP-001-RB/deployment-plan-rebaseline.md` (new)

## Test Coverage Assessment

The new test file correctly exercises the `POST /api/deployment/plans` endpoint
through all four governance-approved stage transitions:

| current_stage | target_stage | transition_type | runtime_action | scale (capital, gross) |
|---|---|---|---|---|
| none | paper | activate | deploy_new_binding | 0.0, 100.0 |
| paper | canary | promote | replace_binding | 5.0, 25.0 |
| canary | live | promote | replace_binding | 100.0, 100.0 |
| live | frozen | freeze | freeze_binding | 0.0, 0.0 |

Each parametrized test verifies first-class plan fields: `plan_id`,
`artifact_id`, `artifact_version`, `approval_decision_id`, `capital_pool_id`,
`current_stage`, `target_stage`, `transition_type`, `runtime_action`, `status`,
and `scale`. Readback via `GET /api/deployment/plans/{plan_id}` is also
verified.

The two guard tests are correct:

- `test_deployment_plan_service_requires_approved_artifact_state`: non-approved
  artifact state rejected with HTTP 422 and expected error detail.
- `test_deployment_plan_service_requires_decided_approval_authority`: an
  undecided approval payload in the request body is rejected with HTTP 422.
  (Note: this test passes the invalid decision in `approval_decision` body
  field, not via the store, so the store-backed validation path is not covered
  here — acceptable for a rebaseline pinning existing behavior.)

The fixture isolates env vars and module reload correctly; temp directories
are cleaned up after each test run.

## Verification Results (Reviewer Environment)

```
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/deployment/test_dep001_rebaseline_service.py -q
→ 6 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/deployment/test_service.py -q
→ 21 passed  (test_service.py has 3 additional tests from in-flight DEP-003 worktree changes)

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s services/control-plane/governance -p 'test_deployment_plan.py'
→ Ran 26 tests in 0.021s — OK
```

The evidence claimed 24 combined (test_dep001_rebaseline_service + test_service).
In the reviewer environment the combined count is 27 (6 + 21), because
test_service.py already contains 3 additional DEP-003 projection tests that
were in the worktree before DEP-001-RB was submitted. This is consistent with
the evidence note: "The working tree already contained unrelated DEP-003
projection changes in services/deployment/test_service.py before DEP-001-RB
edits." The discrepancy does not affect the correctness of the rebaseline.

## Review Notes (ZH)

審查通過：DeploymentPlan 四個階段轉換（none→paper activate / paper→canary promote / canary→live promote / live→frozen freeze）均正確驗證 runtime_action、transition_type 及 scale 欄位；兩個 guard 測試（artifact_state=candidate → 422、approval_decision.decision_state=under_review → 422）行為正確；evidence 說明清楚且範圍限縮於新增測試檔與證據包；governance 26 個單元測試全過。

跨任務附註：test_service.py 的測試數目從 evidence 的 24 combined 增加到 reviewer 環境的 27 combined，原因是 DEP-003 worktree 已預先在 test_service.py 加入 3 個 projection 測試；evidence 已明確說明此 worktree 污染情況，不影響本次審查。
