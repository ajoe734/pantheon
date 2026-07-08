# MGMT-OPS-002 - Frontend Adapters And Data Confidence Display

Owner: Codex

Reviewer: Codex2

Wave: 1

Dependencies:

- `MGMT-OPS-001`

Source plan:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`

## Goal

Normalize frontend data adapters so all management pages render source
confidence, missing data, field names, and action states consistently.

## Required Work

- Audit adapter usage under `execute-plans:src/management` and
  `execute-plans:src/lib`.
- Normalize `snake_case` and `camelCase` backend shapes at the adapter boundary.
- Add display helpers that never render `nan`, `NaN`, `undefined`, or silently
  coerced zero for missing metrics.
- Add common UI states for `formal`, `partial`, `fallback`, `degraded`, and
  `unavailable`.
- Make Persona Fleet performance links preserve persona id, runtime id, period,
  and source hints.
- Add focused tests for the screenshot case: Persona Fleet summary opens
  Performance Attribution as fallback/diagnostic when formal rows are absent.

## Acceptance

- All targeted pages share the same data-confidence labels and empty-state copy.
- `nan` never appears in operator-facing metrics.
- Frontend tests cover field normalization and missing-source rendering.
- Clicking from Persona Fleet to Performance Attribution preserves focus persona
  context and does not claim formal attribution when only fallback exists.

## Artifacts

- `execute-plans:src/management`
- `execute-plans:src/lib`
- `execute-plans:e2e`

## Implementation Evidence

- Added shared management display/data-confidence helpers in
  `execute-plans:src/lib/utils.ts` for snake_case/camelCase field reads,
  missing-value suppression, confidence labels, empty-state copy, and Persona
  Fleet attribution links.
- Updated Performance Review to load Persona Fleet, preserve persona/runtime/
  period/source context in Performance Attribution links, and label links as
  fallback diagnostic when formal attribution rows are absent.
- Updated Promotion & Allocation to use the same confidence labels and
  empty-state copy.
- Added focused coverage in existing tracked frontend suites for field
  normalization, missing metric suppression, shared confidence states,
  Persona Fleet fallback attribution links, and `/management/persona-fleet`
  route mapping.

Verification:

```bash
npm test -- src/management/components/performance-review/ManagementPerformanceReviewPanel.test.tsx src/management/shell/routeRegistry.test.ts src/management/components/promotion-allocation/ManagementPromotionAllocationPanel.test.tsx
npm run build:management
```
