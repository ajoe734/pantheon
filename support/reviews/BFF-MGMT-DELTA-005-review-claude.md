# Review: BFF-MGMT-DELTA-005 — GET /bff/management/risk-radar

Reviewer: Claude
Owner: Codex
Date: 2026-05-24
PR: #532 (merged into dev at a5d7182cf613927e26f6c26c1546f960b7145869)

## Outcome: APPROVED

## Verified

```bash
python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py -q
# 62 passed, 3 pre-existing datetime.utcnow DeprecationWarnings
```

## Acceptance Criteria Check

| # | Criterion | Status |
|---|---|---|
| 1 | Path registered in FastAPI/OpenAPI | ✅ `@app.get("/bff/management/risk-radar")` |
| 2 | Cross-persona/strategy risk indicators: drawdown, exposure, VaR | ✅ All three computed, per-metric indicator statuses included |
| 3 | Anonymous request returns 401 | ✅ `test_risk_radar_requires_read_auth` |
| 4 | Authenticated request returns 200 | ✅ `test_risk_radar_composes_persona_strategy_exposure_drawdown_and_var` |
| 5 | Canonical aggregate envelope | ✅ `data.id`, `data.items/rows/indicators`, `data.summary`, `page_info`, `meta` |
| 6 | CORS preflight returns 204 | ✅ `test_risk_radar_cors_preflight` |
| 7 | Focused pytest: success, auth, preflight | ✅ 3 new tests |
| 8 | execute-plans typed path and fetch helpers | ✅ `ManagementRiskRadarResponse`, `fetchManagementRiskRadar`, `managementRiskRadarPath` |

## Implementation Notes

- Risk thresholds are sound: drawdown (watch 6%, critical 10%), exposure utilization (watch 80%, critical 100%), VaR utilization (watch 5%, critical 10%)
- `_management_risk_overall_state` correctly escalates to the worst indicator; all-unknown produces "unknown" rather than "ok"
- VaR falls back to `abs(exposure × drawdown)` with source annotated as `exposure_x_drawdown` when telemetry VaR is unavailable — this is a reasonable conservative proxy
- Rows sorted by severity severity first, then by VaR/exposure magnitude descending — appropriate for an operator risk dashboard
- `source_refs` tracks full runtime/binding/plan/pool/persona/strategy ID sets per row
- Links to /bff/personas, /bff/strategies, /bff/capital-pools when IDs are known
- Endpoint is read-only; `_require_read_role` enforced; no capital mutations

## No Issues Found

Implementation satisfies all acceptance criteria. PR #532 already merged.
