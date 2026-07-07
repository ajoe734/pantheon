# MGMT-OPS-002 - Frontend Adapters And Data Confidence Display

Owner: Codex2

Reviewer: Claude2

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
