# Task Brief: SUP-DISPATCH-EXPLAIN-TOOL-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a dispatch-explain diagnostic tool for supervisor.py
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Closeout recheck: python3 .orchestrator/test_explain_dispatch.py (14/14), python3 -m py_compile scripts/explain_dispatch.py .orchestrator/test_explain_dispatch.py, and --help passed. PR #4532 remains OPEN at ce2975dbd82de6702a957dced06d03a6f6235c97; required canonical review gate failed with policy=review_before_merge reason=task_state_unavailable, while binding is 4aae794273c6317ab8ef2c0754ef272887836283. Bound review_file at that SHA says Status: todo and has no independent review decision, so it cannot satisfy closeout evidence requirements. Keep review_approved; CI task-state source repair plus fresh exact-head reviewer approval using a committed reviewed manifest are required before merge and done.

## Summary
Composes existing pure dispatch-gate functions into a single read-only CLI that answers 'why was task X not dispatched this tick' without hand-writing a throwaway script against internal functions, as had to be done live on 2026-08-04.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
