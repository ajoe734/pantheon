# Review: BP5-SVC-004 — DeploymentPlan and Stage-Transition Planner API

Reviewer: Claude
Date: 2026-04-15
Outcome: **APPROVED**

---

## Scope Checked

- `services/deployment/service.py`
- `services/deployment/models.py`
- `services/deployment/test_service.py`
- `services/deployment/contract.md`
- `services/deployment/README.md`
- L1 policy: `PAPER_CANARY_LIVE_POLICY.md`
- L1 policy: `ROLLBACK_AND_POSITION_SEMANTICS.md`
- Domain: `services/control-plane/governance/deployment_plan.py`

---

## Test Run

```
8 passed in 1.07s  (/tmp/govtest_pkgs/bin/pytest services/deployment/test_service.py -v)
```

---

## Route Coverage (per contract.md)

| Route | Implemented | Tested |
|---|---|---|
| `POST /api/deployment/plans` | ✅ | ✅ |
| `POST /api/deployment/plans/validate` | ✅ | ✅ (forbidden stage transition) |
| `GET /api/deployment/plans` | ✅ | ✅ (strategy_id filter) |
| `GET /api/deployment/plans/{plan_id}` | ✅ | ✅ |
| `POST /api/deployment/plans/{plan_id}/status` | ✅ | ✅ (valid + invalid transitions) |
| `GET /api/deployment/strategies/{strategy_id}/read-model` | ✅ | ✅ (post-executed read-model) |
| `GET /health` | ✅ | ✅ |

---

## Policy Compliance

**PAPER_CANARY_LIVE_POLICY.md:**
- Stage enum `none/paper/canary/live/frozen` correctly mirrored in `DeploymentStageBody`.
- `scale.capital_scale_pct`, `gross_scale_pct`, `ramp_schedule` all present in `DeploymentScaleBody`.
- Stage-gate validation (paper→canary→live ordering, forbidden skips) delegated to canonical `StagePlanner.derive_transition_type()` — no re-implementation; policy enforced at domain level.
- Rollback linkage required for `paper`, `canary`, `live` target stages — enforced by `DeploymentPlan.validate()` in the domain.

**ROLLBACK_AND_POSITION_SEMANTICS.md:**
- All three rollback action types present: `replace`, `pause_then_replace`, `liquidate_then_replace` in `RollbackActionTypeBody`.
- `RollbackRefBody` carries `target_artifact_id`, `target_version`, `action_type` as required.
- `RuntimeAction` enum values (`freeze_binding`, `resume_binding`, `pause_then_replace`, `liquidate_then_replace`) correctly mirrored.
- Rollback semantics are owned by the domain layer; the service API correctly passes through without overriding.

---

## Design Notes

- **Clean layering**: service.py wraps `StagePlanner` + `DeploymentPlanStore` without re-implementing domain logic. Stage and rollback validation is handled by the canonical control-plane module.
- **Dual error handling** (global `DeploymentPlanError` handler at line 349 + per-route catches): not a bug — create/validate explicitly return 422 while status update returns 404/400 with string-based discrimination. The global handler serves as a backstop for list/read-model routes which don't raise.
- **Module-level singletons** resolved at import: same pattern as BP5-SVC-003; test fixture correctly handles via `importlib.reload`. Acceptable for file-backed v1.
- **Default `status=APPROVED`** in `CreateDeploymentPlanRequest`: consistent with `StagePlanner.create_plan()` default and the intended call flow (plan is created post-approval-decision).

---

## Minor Gaps (Non-blocking, follow-up only)

- No test coverage for `freeze`/`resume` transition paths (`frozen` target stage, `FREEZE_BINDING`/`RESUME_BINDING` runtime actions). These are valid plan types per the domain but untested at the HTTP layer.
- `draft → approved` transition not exercised in tests (all plans created as `approved` by default). Low risk since the status machine itself is tested for invalid and valid transitions.
- `requirements.txt` does not pin versions. Acceptable for service-local v1 baseline.

---

## Conclusion

Implementation delivers the full contract surface, correctly delegates policy enforcement to the canonical domain module, and passes all 8 tests. Service boundary and ownership documented in `contract.md`. Approved for finalization.
