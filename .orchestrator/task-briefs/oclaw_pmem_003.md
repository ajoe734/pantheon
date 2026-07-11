# Task Brief: OCLAW-PMEM-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Canonical memory bridge to OpenClaw workspace
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Review approved: independently verified acceptance criteria against persona_memory_bridge.py and writeback endpoint, 121 integrations/openclaw tests pass, PR #3102 checks green; returned to owner for finalization

## Summary
建立 Memory Plane 到 OpenClaw workspace 的 materialization bridge；OpenClaw workspace 只能是 cache，不是第二個 memory source of truth。
