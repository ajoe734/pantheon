# MGMT-PERF-IA-002 - Performance And Ranking Read Model

Owner: Codex2

Reviewer: Claude2

Wave: 0

Repository: `ajoe734/pantheon`

Dependencies: none

## Goal

Lock the BFF query envelope and source-confidence contract needed by all three
canonical centers without inventing missing data.

## Required Work

- Audit the current portfolio, attribution, persona ranking, quarterly ranking,
  recommendation, capital pool, rebalance, and ranking-policy endpoints.
- Normalize common identifiers and filters: persona, runtime, strategy, capital
  pool, sleeve, artifact, broker, stage, period, and as-of.
- Return explicit formal, partial, fallback, degraded, and unavailable source
  states with freshness, coverage, missing bindings, and observed time.
- Separate ranking snapshots from recommendations and applied actions.
- Ensure recommendations reference immutable ranking evidence and Human Review
  state.
- Add contract tests for complete, partial, fallback, stale, empty, and
  unavailable collections, including zero rebalance/formula rows.

## Acceptance

- Frontend centers can use one identity/query vocabulary.
- Missing joins remain diagnostics rather than dropped rows or `nan` metrics.
- Ranking evidence cannot be mistaken for approval or application.
- Existing consumers remain compatible or have an explicit migration response.
- Pantheon PR is merged to `dev` with tests and merge SHA.

## Artifacts

- `services/control-plane/bff`
- `services/control-plane/bff/tests`
- contract and schema documentation owned by Pantheon
