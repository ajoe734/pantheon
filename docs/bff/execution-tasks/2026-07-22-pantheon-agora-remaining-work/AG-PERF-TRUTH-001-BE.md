# AG-PERF-TRUTH-001-BE — Governed Agora performance projection and suggestion actions

Priority: P0
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex
Reviewer: Claude
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

## Objective

Provide authoritative BFF read models for Strategy Performance detail and
governed adjustment-suggestion actions so the frontend never needs to invent
compliance, interventions, execution history, warnings, or expected effects.

## Owned scope

- Agora performance/Trading Room BFF route module and service projection
- additive Agora OpenAPI/contracts and generated backend fixtures
- focused authz, provenance, idempotency, audit, and projection tests

## Required contracts

The read model must return, per strategy and period:

- compliance metrics and calculation/source identifiers;
- intervention aggregates and evidence references;
- execution-history rows linked to real decision/order/fill/reconciliation
  identities;
- adjustment suggestions with status, provenance, as-of time, and explicitly
  nullable expected effect/risk;
- a top-level freshness/availability envelope.

The command surface must support applying, rejecting, or returning a suggestion
to Workshop. It must require authenticated authorization, idempotency, expected
version/CAS where state can race, append an audit event, and return a durable
receipt plus authoritative readback. It must not route an order or change live
capital.

## Acceptance

- Missing telemetry or lineage produces typed `unavailable`/null fields, never
  generated numbers, securities, dates, P&L, or prose conclusions.
- Tenant/user/role isolation is covered, including viewer refusal for writes.
- Repeating an idempotency key cannot create a second action.
- Apply/reject/return actions survive BFF restart and can be read back by
  receipt ID.
- OpenAPI and focused service/BFF tests pass.
- PR is merged to `dev`; the handoff records exact routes and generated-type
  impact for `AG-PERF-TRUTH-001-FE`.

## Approved delivery handoff

Claude approved the implementation after independently verifying the scoped
live gate, canonical lineage, typed-unavailable behavior, durable receipts,
idempotency/CAS, viewer refusal, and no-order boundary. The repository contract
for `AG-PERF-TRUTH-001-FE` is the additive Agora v1.10 bundle:

- `GET /bff/agora/trading-room/strategies/{strategy_id}/performance`
- `POST /bff/agora/trading-room/strategies/{strategy_id}/performance/suggestions/{suggestion_id}/actions`
- `GET /bff/agora/performance/action-receipts/{receipt_id}`

Frontend generated types must consume `PerformanceProjectionEnvelope`,
`StrategyPerformanceProjection`, `SourceAvailability`, `ComplianceMetric`,
`InterventionRecord`, `ExecutionHistoryRow`, `PerformanceWarning`,
`AdjustmentSuggestion`, `SuggestionProvenance`, `SuggestionActionRequest`,
`SuggestionActionReceipt`, and `SuggestionActionEnvelope` from
`services/control-plane/specs/agora/v11/performance_truth.schema.json`, routed
by `services/control-plane/openapi/agora_v1_10.openapi.yaml` and locked by
`services/control-plane/specs/agora/bundle_index.v1_10.json`. Consumers must
preserve nullable expected effect/risk and typed availability; they must not
synthesize fallback values or treat a local toast as a receipt.

Owner closeout verification on 2026-07-22:

- `python -m pytest -q services/control-plane/bff/agora/performance/test_performance.py`
  — 13 passed (one dependency deprecation warning).
- `python -m compileall -q` over the performance package, Agora router, and BFF
  `main.py`; `jq empty` over all v1.10 JSON artifacts; `git diff --check` — all
  passed.
- `main.app` unauthenticated smoke for all three routes — each returned 401.

This is repository implementation evidence only. Hosted availability and the
cross-surface compatibility/deployment claims remain owned by the downstream
Agora compatibility and hosted-close tasks.

## Exclusions

- No frontend changes.
- No recommendation engine that fabricates suggestions when no governed source
  exists.
- No broker/order route or production write enablement.
