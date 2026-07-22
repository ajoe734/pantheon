# MGMT-OPS-003 - Portfolio Capital And Risk Monitor

Owner: Codex2

Reviewer: Codex

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
- Frontend integration now lives outside this repository after the embedded
  `execute-plans` mirror removal; do not restore the deleted mirror here.

## Implementation Notes

- BFF Portfolio Book holdings and positions now expose normalized identity,
  capital scope, source status, row-level incidents, coverage counters, and
  filters for broker, source status, stale telemetry, and risk state.
- Performance attribution rows expose `data_confidence` so missing or degraded
  holdings remain visible without being treated as formal attribution.
- This publish restores the pantheon-owned BFF contract and task evidence from
  PR #3065 on top of current `dev` without reintroducing the deleted embedded
  frontend mirror.

## Local Validation

- `python3 -m pytest services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py -q`
  - 46 passed.
- Frontend tests for the former embedded `execute-plans` paths are not runnable
  in this repository after `REPO-BOUNDARY-EXECUTE-PLANS` removed the mirror.
