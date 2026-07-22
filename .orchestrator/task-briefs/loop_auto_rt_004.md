# Task Brief: LOOP-AUTO-RT-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add runtime-aware signal isolation
- Status: done (finalized 2026-06-27)
- Owner: Claude2
- Reviewer: Claude
- Next: Closed — PR #2425 merged, done transition complete.

## Summary
把 paper runtime signal consumption 依 runtime 或 binding identity 隔離，移除 shared queue blind consumption 風險。

## Closeout Record

**Finalized by:** Claude2  
**Finalized at:** 2026-06-27  
**Verification:** 63/63 tests pass (31 new isolation tests + 32 existing consumer tests)

### Acceptance Criteria

| Criterion | Status |
|---|---|
| Multiple runtime consumers cannot consume each other's signals | PASS |
| Mismatched runtime/persona/capital-pool signals are rejected | PASS |
| Dead-letter or requeue behavior is tested | PASS |

### Artifacts

- `services/execution/lean_runtime/signal_consumer.py` — runtime_id + capital_pool_id isolation
- `services/execution/lean_runtime/pending_signal_store.py` — DLQ helpers
- `services/execution/lean_runtime/paper_runtime.py` — wires RuntimeIdentity into consumer
- `services/execution/lean_runtime/test_signal_isolation.py` — 31 new isolation tests
- `docs/deployment/evidence/loop-auto-rt-004-signal-isolation.md` — evidence note

### Review Notes (Claude)

- 審查通過
- 31 個新隔離測試全部通過，32 個既有測試無回退
- binding→runtime→capital_pool 隔離順序正確
- DLQ 寫入失敗不阻斷 signal path
- 後向相容：無欄位的舊訊號照常通過
