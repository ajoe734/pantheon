# AG-GAP-008: Implement typed Trading Room SSE stream

## Scope

`GET /bff/agora/trading-room/stream` is an explicit stub returning an empty
SSE response ("Full typed-event streaming is deferred",
`trading_room/router.py:2985-2996`). The v1_3 contract family defines typed
stream events (`workshop_stream_event` pattern; Round2 closure item C set the
first-ack < 2s bar). The workshop SSE stream is already real
(`strategy_workshop/router.py:1490+`) and is the implementation model.

## Work

1. Define/reuse the typed event envelope for trading-room streams (decision
   event lifecycle updates, workspace/version changes, proposal status) in an
   additive spec extension if the v1_3 contracts do not already cover it.
2. Implement the stream on the async-queue pattern used by the workshop SSE,
   scoped per tenant/user, first event (ack or snapshot) within 2 seconds.
3. Emit events from the trading_room store mutation paths so a second browser
   session sees decision-event and workspace changes without polling.

## Acceptance

- `/bff/agora/trading-room/stream` returns typed events, not an empty stream;
  first-ack < 2s measured in the test.
- Events fire on decision-event transitions and workspace accept/patch/rollback.
- Tenant/user scoping proven (no cross-user events).
- Live dev proof: curl SSE transcript captured under
  `docs/deployment/evidence/ag-gap-008/`.

## References

- `services/control-plane/bff/agora/trading_room/router.py:2985-2996`
- `services/control-plane/bff/agora/strategy_workshop/router.py:1490` (model)
- `services/control-plane/specs/agora/v4/workshop_stream_event*`
