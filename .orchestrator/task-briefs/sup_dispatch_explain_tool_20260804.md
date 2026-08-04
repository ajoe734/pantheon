# Task Brief: SUP-DISPATCH-EXPLAIN-TOOL-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a dispatch-explain diagnostic tool for supervisor.py
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Independent review on PR #4532 head 4aae794273c6317ab8ef2c0754ef272887836283: traced primary dispatch order against dispatch_ready_tasks; 14/14 focused tests and py_compile passed; PANTHEON_STATUS_ROOT probe left config, state, and provider-capabilities byte-identical; no supervisor, poll_workers, process_queue, reconcile_queue_records, or BFF changes.

## Summary
Composes existing pure dispatch-gate functions into a single read-only CLI that answers 'why was task X not dispatched this tick' without hand-writing a throwaway script against internal functions, as had to be done live on 2026-08-04.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
