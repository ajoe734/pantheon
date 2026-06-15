# E2E-R6 — Signal idempotency / double-fill prevention

**Round:** E2E-R6 of the e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r6-idempotency
**Business flow:** signal enqueue → worker consume → execute. A repeated signal
(same `signal_id`) must NOT produce a second fill.

## Plan & verification

Live-test double-fill prevention on the deployed fleet via two mechanisms, then
gate the invariant with a service-level regression test.

## Live result (dev, 2026-06-15)

**Same-batch duplicate** (two identical `signal_id` in one drain): collapsed by
per-symbol conflict resolution → **1** `MarketOrder` (not 2).

```
pushed 2 identical signal_id -> "Conflict on AAPL.US: retained newer ..." -> MarketOrder AAPL 2 (once)
```

**Cross-batch duplicate** (same `signal_id` re-enqueued in a later poll):
discarded by the idempotency set → no second fill.

```
batch 1: MarketOrder TSLA 2   (executed)
batch 2 (same signal_id): "Duplicate signal_id — discarding (idempotent)"  (no execution)
```

So double-fill prevention **holds e2e** in-process. Confirmed by unit tests:
`test_signal_consumer.test_drain_records_duplicate_signal_noop_feedback` and a new
service-level `test_drain_once_dedups_duplicate_signal_id_across_polls`
(test_paper_runtime: 21 passed).

## Finding (known limitation)

The dedup set `SignalConsumer._processed_signal_ids` is **in-memory per worker
process**. It is NOT persisted, so a worker restart resets it — a `signal_id`
replayed after a restart would execute again (cross-restart double-fill). For
paper this is low-impact, but for live trading it is a durability gap.

## Disposition

- **Shipped (code/CI):** a service-level (drain_once) cross-poll dedup regression
  test, complementing the existing consumer-level test, so the in-process
  idempotency invariant is gated.
- **Flagged (durability, not fixed here):** persist the processed-signal-id set
  (e.g. in the signal-store / redis with a TTL) so dedup survives worker
  restarts. Non-trivial and out of scope for this round; recorded as the next
  hardening step for idempotency.

## Next round

E2E-R7: telemetry ingest binding-mismatch validation + DLQ health (events must be
validated against the binding store; the DLQ must not silently grow), or deepen
R6 by implementing persistent cross-restart dedup.
