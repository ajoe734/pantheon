# Task Brief: SUP-L12-RUNNING-OWNER-EXACT-HEAD-PR-CLOSEOUT-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Move #4396 running-owner exact-head proof through governed PR/closeout
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Independent exact-head review of PR #4550 @55a0ae95c: core conclusion verified, evidence binding and two factual claims are wrong. VERIFIED TRUE: PR #4396 is MERGED (squash 9cb030dc1b, 2026-08-05T02:00:30Z, merge-base --is-ancestor origin/dev = yes); reconcile row SUP-L12-RUNNING-OWNER-EXACT-HEAD-RECONCILE-20260731 resolves source=archive; #4386 row SUP-L12-RUNNING-OWNER-RECONCILE-20260729 is still todo so it is correctly not counted complete. FIX 1 (binding): evidence.json task.owner=Claude/task.reviewer=Antigravity and independent_review.required_reviewer=Antigravity contradict the canonical pair owner=Antigravity/reviewer=Claude; commit 55a0ae95c trailers are also inverted (LLM-Agent: Claude / Reviewer: Antigravity) and the committed task-brief mirror still reads Status in_progress / Owner Claude. Rebind all four to Antigravity/Claude before re-handoff -- approval freezes the head, so this cannot be repaired afterwards. FIX 2 (false claim): prior_blockers[0] says the --method GET exact-head fix landed via PR #4396 and is resolved_on_dev. Squash 9cb030dc changed only 4 evidence/brief files and zero code; the fix actually landed as 83b6fd035. As of 2026-08-06T11:57:30Z PR #4590 (squash 23ae23c21, 227 files, -47932) deleted review_evidence_file_committed and test_review_evidence_file_committed_uses_exact_head_get_query from dev entirely, so resolved_on_dev is now false -- git grep on origin/dev 4ee7fc95f returns no hits. FIX 3 (stale): subject_pr_4386 records head 43d59e78 merge_state DIRTY mergeable CONFLICTING; live head is bce88797 with MERGEABLE/BLOCKED, so the stated reason for non-completion must be restated as open + row todo + not merged. Note: the failing canonical review gate on 4550 is only the expected pre-approval no-review-proof-tag state, not a defect. Separate fleet issue for Human/Ops, out of this task scope: PR #4590 also deleted .github/workflows/canonical-review-gate.yml from dev while that context stays required by dev branch protection.

## Summary
Resolve the draft ReviewBus PR #4396 integration gap so current-head running-owner support evidence is either governably integrated or explicitly routed without draft-PR blockage.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
