# AG-GAP-008 — Trading Room typed SSE

Status: implementation ready for review

## Delivered contract

`GET /bff/agora/trading-room/stream` now returns a `StreamingResponse` with an
immediate `trading_room.connected` event. Each event carries `id`, `type`,
`timestamp`, typed `data`, and the authenticated tenant/user scope. The
connection acknowledgement retains
`no_order_route_proof=agora_decision_support_only`.

Channels and bounded replay buffers are keyed by both tenant and user. A
`Last-Event-ID` reconnect only replays events from that exact scope. Live
trader-decision writes publish `trading_room.decision.recorded` only to the
writer's channel. Heartbeats keep idle connections open, and response headers
declare replay and aggregate resync behavior.

This task does not add order routing, RuntimeBinding writes, capital binding,
or promotion authority.

## Verification

```text
python3 -m pytest -q \
  services/control-plane/bff/agora/trading_room/test_trading_room_stream.py \
  services/control-plane/bff/agora/trading_room/test_trading_room.py
52 passed

python3 -m py_compile \
  services/control-plane/bff/agora/trading_room/router.py \
  services/control-plane/bff/agora/trading_room/test_trading_room_stream.py

git diff --check
```

Focused coverage proves immediate acknowledgement (well inside the two-second
requirement), typed framing, replay ordering, live delivery, and negative
cross-user isolation.
