# Closeout Evidence: DEVLOOP-PAPER-BINDING-RESTORE-001

Owner: Claude
Reviewer: Codex
Closeout performed: 2026-07-04

## Scope confirmed merged

- PR #2961 (b9b518593): RuntimeBindingStore backup snapshot + restore guard
  for missing/blank/`[]` primary store.
- PR #2963 (093df7910): backfill `.bak` on load when primary is non-empty
  but backup does not exist yet.
- PR #2965 (6ac00acfd): `scripts/ensure_devloop_paper_runtime_worker.sh`
  targets the compose-managed `pantheon-pantheon-paper-runtime-1` /
  `pantheon-paper-runtime` service instead of the deleted `paper-rt-test`
  ghost container; deployed live to `/home/lupin/paper-loop/ensure_worker.sh`
  (verified byte-identical content at closeout time).

All three are ancestors of `origin/dev` at closeout time (fast-forwarded
this task branch from `30ed3b7e7` to `3948ae374`).

## Focused verification run at closeout

```
python3 -m pytest services/execution/runtime-manager/test_runtime_binding.py \
  services/runtime-manager/test_runtime_manager.py \
  services/runtime-manager/test_runtime_hardening.py -q
# 126 passed
```

## Live evidence captured at closeout (2026-07-04T13:25-13:28Z)

The dev compose stack cycled (`pantheon-pantheon-paper-runtime-1` and
`pantheon-runtime-manager-1` both restarted around 13:25 UTC), which gave a
live, unplanned test of binding survival across a restart:

- `docker exec pantheon-runtime-manager-1 cat /data/runtime/runtime_bindings.json`
  shows 2 records: `rb-8dccaedb...` (retired) and `rb-31bd3cf07cc94cecb47d23ee7c1c43ed`
  (active, `runtime_id=rt-devloop-l0-001`, `capital_pool_id=pool-devloop-l0-rescue-1783168661`).
- `/home/lupin/paper-loop/feed_signals.sh` pushes to
  `pantheon:signals:pending:rb-31bd3cf07cc94cecb47d23ee7c1c43ed` — binding_id
  matches the fed queue.
- During the restart window (13:25:01-13:25:46Z) paper-runtime logged
  `RuntimeBinding is required before paper execution can drain signals` /
  `runtime-manager unavailable` while runtime-manager was still starting.
  This self-healed once runtime-manager became reachable; no further drain
  errors were logged afterward.
- `curl localhost:18010/health` after self-heal:
  `binding_lookup.resolved=true`, `binding_id=rb-31bd3cf07cc94cecb47d23ee7c1c43ed`,
  `status=active`, `last_error=null`, `signal_store.queue_depth=0`.
- `pantheon-signal-store-1` (redis) `DBSIZE` shows ~99
  `pantheon:signals:pending:rb-31bd3cf...:processed:<signal_id>` idempotency
  markers — real signal UUIDs from the cron feed, not fixtures.
- Postgres `telemetry_events` table: `event_type IN (heartbeat, pnl_snapshot,
  paper_order_simulated, paper_fill_simulated)` streaming continuously for
  `runtime_id=rt-devloop-l0-001`, `binding_id=rb-31bd3cf07cc94cecb47d23ee7c1c43ed`.
  `paper_fill_simulated` rows carry `metadata.signal_id` values that match
  redis idempotency-marker UUIDs from the live cron feed (e.g.
  `ae183093-11cf-4bdb-a1e5-2a5501c92972`, `6a2d9f52-ff86-4657-ae71-43208d42f7ad`)
  — confirming a real signal -> fill -> telemetry fingerprint chain, not
  synthesized/fixture data.

## Acceptance checklist result

- [x] `runtime_bindings.json` has an active binding for
      `strategy-devloop-l0-001` whose `binding_id` matches the fed queue.
- [x] `pantheon-pantheon-paper-runtime-1` healthy; drain no longer raises
      `RuntimeBinding is required`; queue depth is drained to 0.
- [x] End-to-end real paper fill + `TelemetryEvent` observed with a real
      signal fingerprint (not fixture/synthesized).
- [x] `ensure_worker.sh` targets the live compose-managed container; no
      ghost-container babysitting.
- [x] Binding survived an unplanned container restart during this closeout
      window; existing focused test suite green (126 passed).

SRCLIVE-005 (US dev market data gap) remains a separate, out-of-scope
upstream item and was not touched by this closeout.
