# TJ-E2E-003 - Correlation Envelope Propagation

Owner: Codex
Reviewer: Claude
Wave: 1
Repository: `ajoe734/pantheon`
Dependencies: `TJ-E2E-001`, `TJ-E2E-002`

## Goal

Propagate the versioned correlation envelope through every P0 producer from
strategy/signal origin to broker, ledger and reconciliation.

## Required work and acceptance

- Generate stable journey/correlation/causation IDs at the approved boundary.
- Preserve known upstream identifiers through commands, events and receipts.
- Add idempotency, schema compatibility and no-field-loss contract tests.
- Prove paper happy path, risk reject and broker reject without temporal guessing.
- Merge focused service changes to Pantheon `dev` with migration/rollback notes.

## Delivered contract

- `services/control-plane/specs/trade_journey/correlation_envelope.py` is the
  canonical implementation of `trade-journey-envelope/1`. It mints a trade
  journey only at the signal/decision boundary and rejects unsupported schema
  versions, missing stable identity, invalid environments, and invalid producer
  revisions.
- `services.trade_journey.correlation_envelope` exposes the contract through an
  importable package despite the control-plane directory's hyphenated name.
- `DecisionSignalProducer` accepts an upstream pre-trade envelope. It preserves
  known research, strategy, correlation, trace, and causation identifiers while
  minting the first `journey_id`; an already complete trade envelope is retained
  unchanged for idempotent replay.
- Downstream propagation changes only event-local identity and requires
  `causation_event_id` to equal the preceding `event_id`. Stable field loss or
  replacement fails closed.
- Production boundaries now carry the envelope through executor signal context,
  the signal-driven Taiwan paper HTTP path and its persisted broker-sidecar
  order records (`client_order_id` included), Shioaji adapter order records,
  risk evaluations, paper telemetry, and reconciliation-drift records. The
  `ShioajiSandboxFacade.place_test_order()` path remains a manual sandbox tool
  and does not claim coverage of the signal-driven paper-order boundary.

## Migration and rollback

The new signal field is additive: decisions without `correlation_envelope`
continue to produce the existing signal v1 shape. Producers can migrate one at
a time by adding the envelope, while strict validation prevents partially
correlated messages from entering the queue. Rollback consists of stopping
envelope emission and reverting the signal-producer integration; existing
consumers ignore the additive field and no persisted schema migration is
required.

## Verification

```sh
python3 -m pytest -q services/control-plane/bff/test_trade_journey_correlation_envelope.py \
  services/execution/lean_runtime/test_signal_producer.py \
  services/execution/lean_runtime/test_executor.py \
  services/execution/lean_runtime/test_paper_runtime.py \
  services/broker/test_paper_correlation.py \
  services/broker/sinopac/test_adapter.py \
  services/capital/test_risk_policy.py \
  services/reconciliation-drift/tests/test_reconciliation_drift_consumer.py \
  services/control-plane/bff/test_trade_journey_contract.py
```

The parameterized P0 contract covers paper completion,
risk rejection, and broker rejection using the explicit causal chain rather
than timestamp, symbol, or fixture-only joins; focused tests also exercise the
real producer functions.
