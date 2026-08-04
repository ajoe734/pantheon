# Task Brief: SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-V2-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Supersede Wave0X #4396 governed closeout with current-head spec
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review failed at PR #4468 exact head 1d8cfb4b8eaf4e5cddb76b6a8b8ca07a938b776d: PR is OPEN/BEHIND; Commit trailers and Pantheon canonical review gate fail, and Smoke acceptance is skipped. Do not mask this with review proof. Obtain maintainer authorization for a safe replacement branch or authorized rewrite of pushed overlength commit 7cc9b02fa29de6e8b8b934c401b43f55cd2aa75e (and preserve all required trailers), then return a green exact-head PR. Refresh V2 evidence from live GitHub: #4396 is ba282edd81c00e75d3c96c820922ee3bb9d7f6ac and auto-integrator remains approval_reviewer_mismatch; #4386 evidence is stale, live head is ae014fe404d7d12ebddce8d0c4dc2be050211f09 with DIRTY/CONFLICTING. Do not request approval/closeout until #4468 CI/review/merge and #4396/#4386 protected-closeout gates are satisfied.

## Summary
Supersedes preempted immutable task SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-20260731 after bridge rejected spec update. #4396 is no longer draft but still blocked by merge/root-freeze closeout.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
