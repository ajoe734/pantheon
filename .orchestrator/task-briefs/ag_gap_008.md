# Task Brief: AG-GAP-008

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Implement typed Trading Room SSE stream
- Status: review
- Owner: Codex2
- Reviewer: Claude
- Next: Independently re-verified commit 89c293866. `stream_trading_room` in
  services/control-plane/bff/agora/trading_room/router.py mirrors the
  established `stream_workshop` SSE pattern in
  services/control-plane/bff/agora/strategy_workshop/router.py (immediate
  connect ack, Last-Event-ID replay from a 500-event deque, 30s heartbeat,
  scope-isolated publish/subscribe keyed by tenant+user). Confirmed
  `no_order_route_proof=agora_decision_support_only` is preserved on the
  connect ack and `trading_room.decision.recorded` publishes only to the
  deciding user's own scope. Confirmed openapi/agora_v1_3.openapi.yaml
  already declared this endpoint's typed-SSE contract and this change
  fulfills it without expanding order routing, RuntimeBinding, capital
  binding, or promotion authority. Re-ran
  `python3 -m pytest -q services/control-plane/bff/agora/trading_room/test_trading_room_stream.py services/control-plane/bff/agora/trading_room/test_trading_room.py`
  (52 passed), `python3 -m py_compile` on router.py and the new stream test
  (clean), and `git diff --check HEAD~1 HEAD` (clean). Formal
  `review_approved` transition needs a human or a different reviewer
  identity per this repo's self-approval constraint on Claude reviewing
  automated-pipeline-authored work; status intentionally left at `review`.

## Summary
把 /bff/agora/trading-room/stream 從空 SSE stub 改為 typed event stream（比照 workshop SSE，first-ack<2s，scope 隔離）。
