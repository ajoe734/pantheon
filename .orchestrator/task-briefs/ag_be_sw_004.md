# Task Brief: AG-BE-SW-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Streaming workshop aggregate
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Review approved: SSE stream, < 2s connected ack, 500-event replay buffer, message.ack fan-out, openclaw.degraded and research.progress helpers all correct. 25 tests cover all acceptance paths. Returned to Claude2 for closeout.

## Summary
依 SD §17.2(workshops/{id}/stream)與 §8.3 串接 workshop SSE aggregate:把 message ack、completeness 更新、research progress、version 事件以 streaming 聚合推給前端;首個 acknowledgement < 2s,長任務走 progress。沿用 AG-BE-ID-003 的 SSE 機制與 §8.2 audit 欄位,OpenClaw 降級回 OPENCLAW_UPSTREAM_DEGRADED。
