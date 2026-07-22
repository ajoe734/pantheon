# Task Brief: PPL-ALLOC-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Current state and page inventory guard
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Content verified accurate (bff_create_persona main.py:39833-40060 and App.tsx routes both match claims; page inventory covers all 14 spec rows). Required fix before re-review: PPL-ALLOC-001-CURRENT-STATE-AUDIT.md uses worker-machine-specific file:// links (file:///tmp/pantheon-worker-worktrees/pantheon/ppl-alloc-001/... and file:///home/lupin/code/execute-plans/src/App.tsx) that will not resolve for any other agent, CI, or reviewer. Replace with repo-relative paths for pantheon files (e.g. services/control-plane/bff/main.py#L39833-L40060) and a GitHub URL or plain repo-relative reference for the execute-plans App.tsx route table, matching the convention in docs/04/pantheon_management_console_load_gap_2026-07-01/archive/MGMT-LOAD-001-closeout-2026-07-01.md.

## Summary
盤點 persona 建立、paper/real 晉升、資金權重調整與 management pages，鎖定哪些頁面要保留、改造、降級或 redirect。
