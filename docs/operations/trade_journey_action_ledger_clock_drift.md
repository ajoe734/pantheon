# Trade Journey durable action ledger and clock-drift guard

The BFF dev/production posture uses PostgreSQL for governed Trade Journey
action idempotency. `memory` is supported only for focused tests and isolated
local development.

## Configuration

- `PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_BACKEND=postgres`
- `PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_DSN` (falls back to `DATABASE_URL`)
- `PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_SCHEMA=public`
- `PANTHEON_TRADE_JOURNEY_CLOCK_DRIFT_SECONDS=5`

The ledger atomically reserves an idempotency key before dispatch. A concurrent
identical request receives `ACTION_IN_PROGRESS`; a different payload receives
`IDEMPOTENCY_CONFLICT`; a completed command replays the durable receipt after
process restart. A reserved record is deliberately not auto-retried after a
crash because the downstream side effect may already have occurred. Operators
must reconcile its command/readback before resolving it.

## Clock behavior

Events retain both `occurred_at` and `recorded_at`. If their absolute offset
exceeds the configured threshold, the materializer retains producer time for
audit but uses recorded time as `ordering_at`, followed by producer sequence,
occurred time and event id. This makes rebuilds deterministic without silently
hiding the offset. The projection emits `clock_drift`, metrics expose count and
maximum absolute skew, and the SLO evaluator creates a journey-linked incident.

## Verification

Run:

```sh
python3 -m pytest -q \
  services/trade_journey/test_action_ledger.py \
  services/trade_journey/test_clock_drift.py \
  services/control-plane/bff/test_tj_e2e_008_governed_journey_actions.py \
  services/trade_journey/test_materializer.py \
  services/trade_journey/test_slo_data_quality.py \
  services/control-plane/bff/tests/test_trade_journey_residual_deploy_config.py
```

Set `TEST_DATABASE_URL` to include the restart-persistence integration test.
