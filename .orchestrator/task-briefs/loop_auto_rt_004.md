# Task Brief: LOOP-AUTO-RT-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add runtime-aware signal isolation
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Review approved: all 3 acceptance criteria verified, 63 tests pass (31 new isolation + 32 existing), backward-compat correct, DLQ best-effort — returned to Claude2 for finalization

## Summary
把 paper runtime signal consumption 依 runtime 或 binding identity 隔離，移除 shared queue blind consumption 風險。
