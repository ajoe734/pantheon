# Task Brief: SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile stale failure-streak reaper exact PR head before Wave 0 closeout
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: REOPEN at PR #4395 exact head 6b47c4b20497dd6e88cf71ecd32d86f773a62b81 (== git ls-remote origin task/SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731). CONTENT VERIFIED GOOD: I independently re-confirmed every claim in the rebound manifest -- PR #4385 state CLOSED; PR #4590 (head task/SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729) MERGED into dev at 23ae23c2185d31d2aeacafaa9b051127a6d53136 on 2026-08-06T11:57:30Z and is an ancestor of origin/dev; invalid anchor 9d53a94a265c live on origin/dev (README.md x1, evidence.json x2) and correct anchor 9d53a94a295d71ee49aea6f4b96e47fbcfd29093 0 times; cat-file 265c MISSING / 295d EXISTS; subject row SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729 is status=blocked owner Antigravity reviewer Claude. My prior reopen items (1) push and (2) #4590 disclosure are both satisfied. BLOCKING (CI, not content): required check 'Commit trailers' is FAILURE at 6b47c4b20 (run 31101643966, range origin/dev..6b47c4b20) because BOTH new commits break the hard 72-char subject limit in scripts/git/check_commit_trailers.py -- 6b47c4b20 subject is 106 chars, b10ebc759 is 99. 'Commit trailers' is a required status check on dev, so PR #4395 is unmergeable at this head and approving would freeze it there. The check re-scans the WHOLE PR range, so a follow-up commit cannot clear it. REQUIRED CORRECTION: (1) git reset --soft/--mixed back to edb1698aa6626d84039243d862dfdc33a8f87770 and re-commit the identical content under subjects <=72 chars, then push with --force-with-lease; the task-id prefix is 52 chars so only 20 remain, e.g. 'SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731: anchor rebind' (65) and 'SUP-L12-STALE-REAPER-EXACT-HEAD-RECONCILE-20260731: dev anchor defect' (69). Keep the existing bodies and LLM-Agent/Task-ID/Reviewer trailers verbatim. (2) In the same rewrite, stop asserting review.reviewed_task_head_sha=edb1698aa -- that is the head I reopened, so the manifest currently certifies a rejected head; point it at the rewritten head lineage under review (e.g. a final short-subject commit that sets it to its parent) or state the pushed head explicitly in review.notes. Do NOT change the F1/F2 findings, classification tables, the verification block, decision.protected_merge_still_required_after_repair=true or wave0_dependency_satisfied=false -- those are all correct. Re-verify 'Commit trailers' is green on the new head before handoff.

## Summary
PR #4385 current head differs from the reviewed task row head; reconcile exact-head proof before treating stale failure-streak reaper as a Wave 0 dependency.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
