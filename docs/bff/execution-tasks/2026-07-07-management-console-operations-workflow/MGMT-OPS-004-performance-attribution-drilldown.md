# MGMT-OPS-004 - Performance Attribution Drilldown And Diagnostics

Owner: Antigravity2

Reviewer: Claude2

Wave: 1

Dependencies:

- `MGMT-OPS-001`
- `MGMT-OPS-002`

Source plan:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`

## Goal

Turn Performance Attribution into a trustworthy causal drilldown. When formal
attribution is missing, the page must show fallback summary and missing-source
diagnostics clearly instead of presenting a misleading matched row.

## Required Work

- Build a normalized attribution view model from formal attribution, portfolio
  holdings, runtime summary, and Persona Fleet summary.
- Add a confidence banner for `formal`, `partial`, `fallback`, `degraded`, or
  `unavailable`.
- Split table sections into formal contribution rows, fallback summary rows, and
  diagnostics.
- Show selected persona id, runtime id, period, source timestamps, row counts,
  and source statuses.
- Replace `nan` source rows with explicit diagnostics.
- Add route and component tests for
  `persona-20260528-04688755` with fleet summary present but holdings/formal
  attribution absent.

## Acceptance

- The screenshot scenario is labeled as fallback or degraded diagnostic, not
  formal attribution.
- The top count distinguishes formal matches from fallback summaries and
  diagnostics.
- Missing holdings are explicit and actionable.
- Source status explains whether the operator has enough evidence for review.
- No frontend state mutation bypasses Human Review.

## Artifacts

- `services/control-plane/bff`
- `execute-plans:src/management/pages`
- `execute-plans:src/lib`
- `execute-plans:e2e`
