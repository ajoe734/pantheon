# MGMT-OPS-001 - Operations Read Model And Source Confidence Contract

Owner: Claude2

Reviewer: Codex2

Wave: 0

Dependencies: none

Source plan:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`

## Goal

Define and implement the shared BFF operations read model that lets Persona
Fleet, Portfolio Book, Performance Attribution, Persona League, Quarterly
Ranking, and Human Review speak the same language about identity, performance,
source confidence, and action state.

## Required Work

- Audit the relevant BFF endpoints used by the management console pages.
- Define common identity fields: persona id, runtime id, ledger id, pool/sleeve
  id, strategy id, artifact id, broker id, period, and `as_of`.
- Define data-confidence states: `formal`, `partial`, `fallback`, `degraded`,
  and `unavailable`.
- Define source status fields: source name, status, row count, freshness,
  coverage ratio, and error/diagnostic message.
- Add or update BFF tests proving the focus persona
  `persona-20260528-04688755` can be represented without losing runtime,
  attribution, and holdings diagnostics.
- Document any upstream data source that cannot yet produce formal attribution
  or holdings for that persona.

## Acceptance

- Pages can consume a shared read model without inventing incompatible local
  fallback semantics.
- Missing joins are represented as diagnostics, not dropped rows or `nan`.
- Fallback summary is distinguishable from formal attribution in the API
  payload.
- Unit or contract tests cover normal, partial, fallback, degraded, and
  unavailable states.
- The implementation does not mutate live capital or broker state.

## Artifacts

- `services/control-plane/bff`
- `services/control-plane/bff/tests`
- `docs/04/pantheon_management_console_operations_workflow_2026-07-07`
