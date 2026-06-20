# Task Brief: AG-BE-000

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora BFF router package and capability manifest
- Status: done
- Owner: Claude2
- Reviewer: Claude
- Next: Closeout finalized. 14/14 tests pass, CI green, PR #1762 merged into dev.

## Summary
依 SD §22.2 在既有 BFF 新增 services/control-plane/bff/agora/ package(router.py/models.py + identity/servant/strategy_workshop/research/trading_room/dashboard/shadow/personalization/management_projection 骨架),以 package router 機制掛載,禁止把 Agora endpoint 塞進單一 main.py。先建 router 骨架 + envelope(§18)+ typed error,實際 handler 由後續 ID/SW 任務填。

## Verification

Closeout verification by Claude2 (2026-06-20):

```
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -v
14 passed in 13.65s
```

CI checks on PR #1762: Commit trailers / Runtime mirror guard / Smoke acceptance — all SUCCESS.

Reviewer (Claude) approved: 14/14 tests pass, §18 envelope correct, capability filtering in place, no route conflicts.
