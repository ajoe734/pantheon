# Task Brief: DEVLOOP-TELREAD

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: BFF telemetry read real store (stop synthesize-on-read)
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Owner closeout by Codex after owned_finalize_dispatch; publish PR #1578 to dev, then run ai-status done after merge.

## Summary
修 BFF /api/v1/telemetry:當 telemetry store 有真實事件時讀真實 store,不要 local_snapshot 現合成;保留 store 空時的 fallback 但標示 source。加測試。

## Closeout

- Review approval: Claude2 approved real-store priority and fallback labelling; see `services/control-plane/bff/review_devloop_telread_claude2_approved.md`.
- Verified: `python3 -m pytest services/control-plane/bff/test_devloop_telread_telemetry_contract.py -q` -> 2 passed on 2026-06-14.
- Publication: task branch `task/DEVLOOP-TELREAD`, PR #1578 to `dev`.
