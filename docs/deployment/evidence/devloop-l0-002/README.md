# DEVLOOP-L0-002 Evidence

Task: `DEVLOOP-L0-002`
Date: 2026-06-14
Owner: Codex
Reviewer: Claude

## Target Runtime

- Paper runtime container: `pantheon-paper-runtime-0260531-1715d8d2`
- Runtime ID: `rt-rescue-0260531-1715d8d2`
- RuntimeBinding ID: `rb-016ccb04e393494ba03de50ccf481d71`
- Strategy ID: `strategy-rescue-0260531-1715d8d2`
- Redis queue: `pantheon:signals:pending:rb-016ccb04e393494ba03de50ccf481d71`
- Redis container: `pantheon-signal-store-1`

The runtime did not set an explicit `PANTHEON_SIGNAL_QUEUE_KEY`; its
`RedisPendingSignalStore` therefore derives the binding-scoped key from
`PANTHEON_RUNTIME_BINDING_ID`.

## Commands

```bash
docker exec pantheon-signal-store-1 redis-cli RPUSH \
  pantheon:signals:pending:rb-016ccb04e393494ba03de50ccf481d71 \
  '<AAPL schema-v1 payload>' '<MSFT schema-v1 payload>' '<NVDA schema-v1 payload>'

docker exec pantheon-paper-runtime-0260531-1715d8d2 python -c \
  "import urllib.request; req=urllib.request.Request('http://127.0.0.1:8010/api/runtime/drain', data=b'{}', headers={'Content-Type':'application/json'}, method='POST'); print(urllib.request.urlopen(req, timeout=10).read().decode())"

docker exec pantheon-paper-runtime-0260531-1715d8d2 python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8010/api/runtime/orders', timeout=5).read().decode())"

docker exec pantheon-signal-store-1 redis-cli LLEN \
  pantheon:signals:pending:rb-016ccb04e393494ba03de50ccf481d71
```

## Result

- `RPUSH` returned queue length `3`.
- `POST /api/runtime/drain` returned `status=ok`.
- The scoped Redis queue drained back to `0`.
- Runtime `processed_signal_count` increased to `4` and `execution_event_count`
  increased to `4`, including the one pre-existing paper fill.
- `/api/runtime/orders` returned the three DEVLOOP-L0-002 events for AAPL, MSFT,
  and NVDA.
- All DEVLOOP-L0-002 events are `paper_fill_simulated` with
  `submitted_to_broker=false`.

## Artifacts

- `signal-enqueue.response.json`
- `paper-runtime-drain.response.json`
- `paper-runtime-orders.response.json`
