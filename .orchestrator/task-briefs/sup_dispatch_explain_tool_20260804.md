# Task Brief: SUP-DISPATCH-EXPLAIN-TOOL-20260804

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add a dispatch-explain diagnostic tool for supervisor.py
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Closeout is held: PR #4532 is behind `dev`; its last canonical gate at `fabdd21ac` failed with `task_state_unavailable`. Integrate `dev` #4546's git-native gate fix, then obtain a fresh Codex approval/review-proof tag at the exact new head. The currently bound brief at `4aae7942` was `Status: todo` and does not record an independent review decision; do not run `done` first.

## Summary
Composes existing pure dispatch-gate functions into a single read-only CLI that answers 'why was task X not dispatched this tick' without hand-writing a throwaway script against internal functions, as had to be done live on 2026-08-04.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
