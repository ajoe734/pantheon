# Task Brief: SUP-DISPATCH-EXPLAIN-TOOL-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a dispatch-explain diagnostic tool for supervisor.py
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Closeout blocked: PR #4532 head 8bd88719e70f304224d6d63bbb60c0663d2408a9 is open and blocked because the required canonical-review gate reports task_state_unavailable. The reviewer binding remains at 4aae794273c6317ab8ef2c0754ef272887836283. Repair the CI task-state source, then Codex must bind approval to the exact current PR head; keep review_approved and do not run done until PR #4532 merges into dev.

## Summary
Composes existing pure dispatch-gate functions into a single read-only CLI that answers 'why was task X not dispatched this tick' without hand-writing a throwaway script against internal functions, as had to be done live on 2026-08-04.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
