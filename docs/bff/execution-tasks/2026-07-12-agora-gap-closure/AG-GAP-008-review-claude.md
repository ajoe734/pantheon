# AG-GAP-008 — reviewer re-verification note (Claude)

Independently re-verified commit `89c293866` against the acceptance criteria
in [AG-GAP-008-trading-room-typed-sse.md](AG-GAP-008-trading-room-typed-sse.md).

- `stream_trading_room` in
  `services/control-plane/bff/agora/trading_room/router.py` mirrors the
  established `stream_workshop` SSE pattern in
  `services/control-plane/bff/agora/strategy_workshop/router.py`: immediate
  connect ack, `Last-Event-ID` replay from a 500-event deque, 30s heartbeat,
  and scope-isolated publish/subscribe keyed by tenant + user.
- `no_order_route_proof=agora_decision_support_only` is preserved on the
  connect ack.
- `trading_room.decision.recorded` publishes only to the deciding user's own
  scope; no order-routing, `RuntimeBinding`, capital-binding, or
  promotion-authority surface is touched.
- `openapi/agora_v1_3.openapi.yaml` already declared this endpoint's typed-SSE
  contract; this change fulfills it without expanding scope.

Re-ran:

```text
python3 -m pytest -q \
  services/control-plane/bff/agora/trading_room/test_trading_room_stream.py \
  services/control-plane/bff/agora/trading_room/test_trading_room.py
52 passed

python3 -m py_compile router.py test_trading_room_stream.py   # clean
git diff --check                                              # clean
```

Formal `review_approved` transition needs a human or a different reviewer
identity per this repo's self-approval constraint on Claude reviewing
automated-pipeline-authored work; task status is intentionally left at
`review` pending that action. PR #3440 already has human-enabled auto-merge
(`enabledBy: ajoe734`, not a bot); once required CI checks pass it can merge
on that basis without a separate written approval artifact.
