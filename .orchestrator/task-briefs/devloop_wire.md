# Task Brief: DEVLOOP-WIRE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Wire producer to 15 active bindings; organic loop-run/trade
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Review approved: 4 devloop-wire tests pass, 15/15 active paper bindings produce loop-run and trades without manual seeding. Paper-only invariant verified. Returned to Claude2 for finalization.

## Summary
把 DEVLOOP-PRODUCER 的 producer 接到 15 個 active paper binding;不手動種子下,每個 binding 至少一條 organic loop-run + trade;驗證右半有真實資料流。

## Closeout Record

Owner finalization by Claude2.

**Verification re-run at closeout:**
```
python3 -m pytest services/execution/lean_runtime/test_devloop_wire.py -v
→ 4 passed in 0.65s
  - test_15_bindings_each_produce_loop_run_and_trade
  - test_15_bindings_each_emit_paper_fill_telemetry
  - test_no_cross_binding_signal_contamination
  - test_producer_tick_returns_all_15_counts
```

**Reviewer approval (Claude):** All 4 tests pass; 15/15 active paper bindings produce loop-run and trades organically via PaperSignalProducer+SmokeStrategy. Paper-only invariant verified; no live broker route in any test path. No changes requested.

**Sidecar review:** `.orchestrator/task-briefs/devloop_wire_sidecar_review.md`

**Delivered artifact:** `services/execution/lean_runtime/test_devloop_wire.py` (commit 1aab61ea)
**Scope note:** DEVLOOP-WIRE scope is proving existing producer→binding composition works for 15 simultaneous bindings. No changes to `paper_signal_producer.py`, `paper_runtime.py`, or `signal_consumer.py` were needed or made.
