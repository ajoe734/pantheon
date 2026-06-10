# Review: MPOS-P1-GOV-001

Reviewer: Claude2
Date: 2026-06-09
Status: approved

## Scope Reviewed

Commit ef00ec7a — `MPOS-P1-GOV-001: add canary stage to promotion gate`

Files reviewed:
- `services/registry/promotion/gate.py`
- `services/registry/promotion/test_gate.py`
- `services/registry/promotion/smoke_test_gate.py`

Cross-checked against:
- `PAPER_CANARY_LIVE_POLICY.md` (L1 canonical policy)
- `services/control-plane/governance/deployment_plan.py` (StagePlanner)

## Findings

### Correctness

1. **PromotionState.CANARY enum** — correctly inserted between PAPER and LIVE, consistent with deployment stage ordering.

2. **validate_transition** — enforces `paper → canary → live`; `paper → live` skip raises `PromotionError`. Aligns exactly with `StagePlanner._stage_order` in `deployment_plan.py` which already had `PAPER:1, CANARY:2, LIVE:3`.

3. **_LIFECYCLE_TO_DEPLOYMENT_STAGE fix** — `retired → "none"` is correct; `retired` is an artifact lifecycle state, not a deployment stage. The previous `"frozen"` mapping was a semantic error.

4. **_LIFECYCLE_TO_ARTIFACT_STATE** — `canary → "approved"` correct; canary stage implies the artifact has been approved for real-capital deployment.

5. **check_requirements for CANARY** — validation covers:
   - `evaluation_summary` with `risk_review_passed` and `sharpe_ratio` (policy §6.1 performance and risk conditions)
   - explicit `approver` (policy §8 canary approver requirement)
   - lineage with source reference (policy §6.1 governance condition)
   - rollback metadata with `_validate_rollback` guard (policy §6.1 rollback_target condition)

6. **build_execution_projection** — rollback guard extended to `(CANARY, LIVE)`. Correct; canary execution requires rollback readiness.

### Policy Alignment

All implemented requirements map to PAPER_CANARY_LIVE_POLICY §5–8:
- Paper → Canary requires: risk review, Sharpe metric, approver, lineage, rollback ✓
- Canary → Live requires: approver, lineage, rollback ✓ (eval conditions already locked in at canary gate)

### Test Coverage

- `test_live_promotion_via_canonical_canary_path`: full paper→canary→live path tested ✓
- `test_paper_to_live_skip_is_forbidden`: transition guard tested ✓
- `test_live_promotion_rejects_without_rollback_registry_reference`: updated for canary→live path ✓
- 121 total tests pass ✓
- Smoke test confirms all expected paths and failure modes ✓

### Scope Boundary

`deployment_plan.py` intentionally unchanged (documented in commit body). The `_legacy_promotion_state` function in deployment_plan.py correctly excludes canary from its legacy alias path per existing test `test_canary_projection_keeps_deployment_stage_without_legacy_alias`. This is correct: the legacy alias only applies to paper/live.

## Acceptance Criteria Verification

| Criterion | Met |
|---|---|
| One canonical promotion path (candidate→paper→canary→live) | ✓ |
| Registry approval cannot bypass governance evidence | ✓ (check_requirements enforces eval_summary, approver, lineage, rollback) |
| Rollback target consistent across paper/canary/live | ✓ |
| Tests cover draft→candidate→paper→canary→live→retired | ✓ |

## Disposition

Approved. No required changes. Owner may proceed to closeout.
