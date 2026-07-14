# TJ-E2E-008 Governed Journey Actions — Owner Handoff

Owner: Codex
Reviewer: Claude
Anchor: `800b37ea8`

## Delivered scope

- Adds `POST /bff/management/trade-journeys/{journey_id}/actions` to the canonical Trade Journey router.
- Admits only the contextual actions `escalate`, `human_review`, `pause`, `cancel`, `reconciliation_retry`, and `incident_acknowledge`.
- Enforces operator RBAC, tenant/journey existence protection, explicit confirmation, `Idempotency-Key`, expected materializer revision, and a fail-closed live-action feature flag.
- Sends admitted commands only through an injected canonical dispatcher. The router does not mutate runtime, broker, reconciliation, incident, or governance state directly.
- Returns a receipt and requires downstream readback. A nominal success without readback is surfaced as `partial_failure`; every response directs the client to refetch canonical state.
- Preserves Human Inbox destination and Trade Journey return context for review/escalation flows.

## Acceptance evidence

Command:

```text
python3 -m pytest -q \
  services/control-plane/bff/test_tj_e2e_008_governed_journey_actions.py \
  services/control-plane/bff/test_tj_e2e_005_trade_journeys_read_api.py
```

Result: `30 passed, 4 warnings in 12.45s` (warnings are existing FastAPI `on_event` deprecations).

The focused tests prove operator success with receipt/readback/refetch, viewer denial, stale revision conflict, idempotent replay, idempotency payload conflict, missing-readback partial failure, and Human Inbox return context.

## Deliberate boundaries / follow-up

- The production router uses a fail-closed unconfigured dispatcher until the canonical runtime/broker/reconciliation/incident command adapters are explicitly composed. It never reports optimistic success.
- No execute-plans source was copied into Pantheon. TJ-E2E-006 is already merged; frontend action controls must consume this endpoint in the separate `execute-plans` repository after the production dispatcher composition is approved.
- Live-capital pause/cancel/reconciliation retry remain disabled unless `PANTHEON_TRADE_JOURNEY_LIVE_ACTIONS=true`; no real-capital action was attempted.
- Broker sandbox proof remains a hosted/integration acceptance item because this task does not bypass the broker adapter and the production dispatcher is intentionally not fabricated in unit tests.

## Review request

Please verify the no-bypass boundary, admission/error semantics, receipt/readback requirement, and whether downstream dispatcher composition should be required in this task or assigned as a narrow follow-up before frontend enablement.
