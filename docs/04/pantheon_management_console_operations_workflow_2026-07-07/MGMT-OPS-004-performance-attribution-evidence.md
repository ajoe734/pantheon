# MGMT-OPS-004 — Performance Attribution Evidence

Status: implementation evidence for the Wave 1 performance-attribution drilldown and fallback diagnostic page in the management console.

Owner: Antigravity

Reviewer: Claude

## What was implemented

- **BFF client library expansion**: Added `getOperationsReadModel` API fetch method in `execute-plans/src/lib/bff-v1/management.ts` and `paths.ts` pointing to the BFF endpoint `/bff/management/operations-read-model/{personaId}`.
- **Attribution drilldown and confidence banner**: Integrated the BFF operations read model into the frontend in `execute-plans/src/management/components/performance-review/ManagementPerformanceReviewPanel.tsx`. It now renders:
  - A color-coded confidence banner indicating the overall data confidence state (`formal`, `partial`, `fallback`, `degraded`, `unavailable`).
  - Formal vs Fallback section routing. If the confidence is fallback/degraded/unavailable, the panel renders a detailed fallback summary block containing performance delta, Sharpe, drawdown, and score metadata, preventing empty table rows.
  - Active source statuses and coverage cards listing row count and health state for each underlying BFF ingestion source.
  - Actionable diagnostics panels. In particular, a red actionable warning triggers for `MISSING_HOLDINGS_MATCH` prompting the operator to inspect ledger runtime bindings or capital pool activity.
- **Metric sanity and label safety**:
  - Replaced raw `nan`/`undefined` values in metric cells with clean fallback placeholders (e.g. "source returned null") to ensure robust rendering.
  - Replaced the pre-existing hardcoded fallback persona label "Crypto-Alt-Hunter" with a generic "Persona label unavailable" placeholder when `readModel?.identity.persona_label` is empty.
- **Test coverage**:
  - Added frontend test cases in `execute-plans/src/management/components/performance-review/ManagementPerformanceReviewPanel.test.tsx` validating the fallback rendering layout, the confidence banner, the actionable diagnostics panel, and the regression test ensuring no hardcoded persona name is displayed when the label is null.

## Verification

### 1. Backend Contract & Integration Tests
Run command:
```sh
pytest services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py
```
Output:
```text
======================= 11 passed, 8 warnings in 22.51s ========================
```

### 2. Frontend Component & Regression Tests
Run command in `execute-plans/`:
```sh
npx vitest run src/management/components/performance-review/ManagementPerformanceReviewPanel.test.tsx
```
Output:
```text
 Test Files  1 passed (1)
      Tests  8 passed (8)
   Start at  14:13:04
   Duration  6.61s
```

### 3. Frontend App Compilation / Build
Run command in `execute-plans/`:
```sh
npm run build:management
```
Output:
```text
vite v5.4.21 building for production...
✓ 1546 modules transformed.
dist/management/assets/app-zJ906pXe.js  403.80 kB │ gzip: 105.39 kB
✓ built in 9.95s
```

## Branch, PRs and Merge Commits

- Task Branch: `task/MGMT-OPS-004` (merged into `dev`)
- Merged Pull Requests:
  - **PR #3068** (MGMT-OPS-004: Performance attribution drilldown and diagnostics):
    - Merge Commit: `6753c2975baa2a4f31cad2e374c0d170beb45f61`
  - **PR #3070** (MGMT-OPS-004: fix fallback persona label in performance attribution):
    - Merge Commit: `7051b056164a3042b7af913fe8b4b5a7038da382`

## Residual Risk

No residual risks identified. The UI changes conform to the read-only operations model for performance attributions and do not perform any direct state mutations or bypass human review boundaries.
