# E2E-R16 — Persistent signal dedup across worker restarts (R6 fix)

**Round:** E2E-R16 (second campaign)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r16-persist-dedup
**Fixes:** the durability limitation flagged in E2E-R6 — signal idempotency was
in-memory per worker process, so a `signal_id` replayed after a worker restart
could double-fill.

## Change

`services/execution/lean_runtime/pending_signal_store.py`:
- `RedisPendingSignalStore` gains `mark_processed(signal_id)` / `is_processed(signal_id)`
  backed by per-id redis keys with a 24h TTL (`<queue_key>:processed:<sid>`). The
  TTL matches the consumer's 24h staleness window (older duplicates are discarded
  as stale anyway), so the dedup window is bounded and redis growth is capped.
  Both ops are best-effort (a redis hiccup never breaks execution).
- `InMemoryPendingSignalStore` gains the same methods (a set) for tests/dry-runs.

`services/execution/lean_runtime/signal_consumer.py`:
- `_is_duplicate()` now also consults the store's persistent `is_processed` (only
  when it returns a real `True`, so a `MagicMock` test store is not mistaken for a
  real dedup store), and caches the hit in-memory.
- A new `_remember_processed()` records each processed id both in-memory and
  (best-effort) in the persistent store; the five processed-id recording sites now
  route through it.

## Result

`test_signal_consumer` (31) + `test_paper_runtime` (21) pass, including new
`TestPersistentDedup` cases: a second `SignalConsumer` (fresh in-memory set —
simulating a restarted worker) still discards a `signal_id` the first consumer
processed, via the shared store's persistent window.

## Disposition

- **Shipped (code/CI):** the persistent-dedup fix + regression tests. Turns the
  E2E-R6 flagged limitation into a fix: idempotency now survives worker restarts.
- **Rollout:** worker image rebuilt so spawned paper-runtime workers carry it.

## Next round

E2E-R17: auth boundary / capability enforcement, then consolidation (R20) which
also wires the recent script verifiers into CI via a single glob block.
