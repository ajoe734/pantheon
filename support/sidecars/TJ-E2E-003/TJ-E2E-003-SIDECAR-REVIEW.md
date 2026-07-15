# TJ-E2E-003 Sidecar Review Packet

Task: `TJ-E2E-003-SIDECAR-REVIEW`
Parent: `TJ-E2E-003`
Owner: Codex2
Reviewer: Codex
Disposition: handoff ready; parent remains blocked

## Review boundary

This packet is support-only. It records review evidence and the remaining
integration gap; it does not change the correlation contract, runtime code,
registry, governance policy, or parent-task acceptance decision.

## Evidence summary

| Boundary | Evidence inspected | Result |
|---|---|---|
| Signal to executor context | `services/execution/lean_runtime/executor.py` copies `correlation_envelope` and `client_order_id` into order-adapter context | Present |
| Risk evaluation | `services/capital/risk_policy.py` accepts and propagates an incoming envelope with producer `risk.evaluation` | Present |
| Paper telemetry | `services/execution/lean_runtime/paper_runtime.py` propagates the context envelope into emitted runtime telemetry | Present |
| Reconciliation drift | `services/reconciliation-drift/consumer.py` propagates the incoming envelope into its result | Present |
| Shioaji adapter model | `services/broker/sinopac/adapter.py` and its focused test preserve `client_order_id` and propagate the envelope | Present, but not sufficient for the live signal-driven paper route |
| Signal-driven paper broker route | `executor -> SubmitTaiwanBrokerOrder -> HTTP -> services/broker/main.py -> paper_simulation.py` | Blocking: the request/runtime boundary does not yet preserve `client_order_id` and `correlation_envelope` end to end |

## Reviewer-critical finding

The focused `ShioajiBrokerAdapter.submit()` coverage is not evidence for the
production signal-driven paper route. The latter crosses the LEAN runtime HTTP
submission boundary and terminates in the broker service paper simulator.
Until that request model, handler, and simulator receipt carry the same
`client_order_id` and propagated envelope, the parent acceptance claim—paper
signal to reconciliation without a temporal/manual join—is not proven.

This is consistent with the parent task's latest durable status and the second
round review note in `docs/reviews/2026-07-12-tj-e2e-003-claude-review.md`:
executor, risk, paper telemetry, and reconciliation-drift propagation are
accepted; the real broker path remains the blocking gap.

## Required parent follow-up

1. Extend `SubmitTaiwanBrokerOrder` and its HTTP payload with
   `client_order_id` and `correlation_envelope`.
2. Extend the broker service request/handler and paper simulator order/receipt
   models to preserve those values and propagate the envelope at the broker
   event boundary.
3. Add a production-path test that traverses the HTTP submission route and
   proves stable identity plus causal continuity in the resulting broker
   receipt/event.
4. Re-run the focused executor, paper-runtime, broker, risk, reconciliation,
   and correlation contract suites before requesting parent re-review.

## Handoff

Reviewer `Codex` should use this packet as a concise evidence index, not as an
approval. Parent owner `Codex` decides how to compose the remaining broker-path
fix into `TJ-E2E-003`; canonical acceptance remains with parent reviewer
`Claude`.
