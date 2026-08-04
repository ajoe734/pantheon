# Task Brief: SUP-DISPATCH-EXPLAIN-TOOL-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a dispatch-explain diagnostic tool for supervisor.py
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude2
- Next: Corrective follow-up for reviewed PR #4532 is ready for Claude2 review. It now loads all runtime diagnostic paths from `PANTHEON_STATUS_ROOT`, fails closed when canonical state is unreadable, and reports the review-redispatch and reassignment skips that the primary dispatch loop applies.

## Summary
Composes existing pure dispatch-gate functions into a single read-only CLI that answers 'why was task X not dispatched this tick' without hand-writing a throwaway script against internal functions, as had to be done live on 2026-08-04.

## Corrective Follow-up

- Resolves relative supervisor runtime paths under `PANTHEON_STATUS_ROOT`, including state, queue, approval queue, activity log, and provider report inputs; no config file is modified.
- Raises a non-zero CLI error instead of silently using an empty state snapshot.
- Mirrors the primary-path failure-loop and chair-reassignment skips, and recognizes the governed `REASON_REVIEW_READY` cooldown redispatch exception.
- Removes the shadowed duplicate all-clear test and adds regression coverage for the state-root, read-only runtime-path, auto-dispatch, failure-loop, chair-triage, and review-redispatch cases.
- Verified with `python3 .orchestrator/test_explain_dispatch.py` (14 tests), `python3 -m py_compile scripts/explain_dispatch.py .orchestrator/test_explain_dispatch.py`, and a canonical-root CLI invocation with no repository mutation.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
