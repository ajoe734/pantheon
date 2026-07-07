# MGMT-OPS-003 - Portfolio Capital And Risk Monitor

Owner: Gemini2

Reviewer: Codex2

Wave: 1

Dependencies:

- `MGMT-OPS-001`

Source plan:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`

## Goal

Make Portfolio Book the operator's first stop for capital, exposure, telemetry
coverage, stale data, and risk incidents.

## Required Work

- Normalize portfolio, holdings, capital pool, sleeve, persona, and runtime
  identifiers.
- Show paper ledger, canary sleeve, and live capital pool separation.
- Add coverage counters: source row count, runtime count, telemetry runtime
  count, stale rows, missing bindings, and degraded sources.
- Add filters for stage, broker, runtime, source status, stale telemetry, and
  risk state.
- Link each row or incident to Persona Fleet, Performance Attribution, and Human
  Review when identifiers are available.
- Add BFF/frontend tests for degraded holdings and missing focus-persona
  holdings.

## Acceptance

- Portfolio Book answers where capital is, which persona/runtime owns exposure,
  and whether telemetry is reliable.
- Missing or degraded holdings for a persona are visible incidents.
- Paper, canary, and live exposure cannot be visually confused.
- Tests prove degraded source coverage does not produce false formal attribution
  downstream.

## Artifacts

- `services/control-plane/bff`
- `execute-plans:src/management/pages`
- `execute-plans:src/lib`
- `execute-plans:e2e`
