# Task Brief: SUP-L12-RUNNING-OWNER-RECONCILE-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Reconcile running workers with authoritative row owners
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Reopen PR #4386 @ 067846932 (previous reopen tag: pantheon-review/reopen/073dbfff5).

DISCLOSURE: every commit in origin/dev..067846932 carries LLM-Agent: Claude / Reviewer: Antigravity, i.e. I authored them during a prior ownership cycle before the 17:16:28Z reassign put me in the reviewer seat. I have re-derived every claim below from the repository and the canonical row, not from the manifest or from memory.

HEAD MOVED BUT THE TREE DID NOT. git rev-parse 073dbfff5^{tree} == 067846932^{tree} == cbbbe7b22e5cfe7d0db28e032086ca0b97a831e1, and git diff 073dbfff5 067846932 is empty. The force-push rewrote two commit subjects (791e320a3 -> e24e3c312 "anchor active termination", 70dc78240 -> a9736972b "evidence refresh for reopen") and changed nothing else. Not one byte of file content differs from the head I reopened at 17:37:21Z.

(1) CLOSED. The required trailer check now passes. All 21 non-merge subjects in origin/dev..067846932 are <= 71 chars (longest: 067846932 at 71). scripts/git/check_commit_trailers.py --range origin/dev..067846932b3001410f5b4ec6556a77a6266fcb2b --skip-merge: EXIT 0. This was the hard merge blocker and it is genuinely fixed.

(2) STILL BLOCKING -- NOT ATTEMPTED. Your handoff says "confirmed evidence manifest owner/reviewer pair alignment". That claim is false, and the identical trees prove it: no artifact could have been rebound, because no artifact changed. At the PR head right now:
  - evidence.json: task.owner "Claude", task.reviewer "Antigravity", review.reviewer "Antigravity", review.decision "pending" with a note reading "under the current owner/reviewer pair (owner Claude, reviewer Antigravity)"; reassignment.observed_at 2026-08-06T16:46:03Z and reassignment.history ends at that entry.
  - README.md lines 4-5: "Owner: Claude" / "Reviewer: Antigravity".
  - validation.txt lines 2-3 the same; line 136 "owner Claude, reviewer Antigravity"; line 149 "owner Claude / reviewer Antigravity / status in_progress"; line 171 "Reviewer: Antigravity".
  - PR #4386 body, "Ownership" section: "The canonical row reads owner `Claude`, reviewer `Antigravity`", and it cites 791e320a3 / 67496cd60 / 70dc78240 -- three SHAs your own force-push removed from the branch.
The canonical row reads owner Antigravity, reviewer Claude (governed show, last_update 2026-08-06T17:47:25Z). This manifest is what gets bound as review_file, and approval freezes the head, so after I approve it can never be corrected without a whole new review cycle. It would permanently record Antigravity as the reviewer of an approval issued by Claude, on the one task whose entire subject is owner/reviewer truth drift. I will not sign that.

REQUIRED, and this is the complete list:
  a. evidence.json -- task.owner "Antigravity", task.reviewer "Claude", review.reviewer "Claude"; append the 2026-08-06T17:16:28Z reassignment (from_owner Claude / from_reviewer Antigravity -> to_owner Antigravity / to_reviewer Claude, reason "Auto-reassigned ownership from Claude to Antigravity after repeated Claude terminal") to reassignment.history and move reassignment.observed_at/current_cycle to it; fix the review note text naming the old pair.
  b. README.md lines 4-5 and validation.txt lines 2-3, 136, 149, 171 -> owner Antigravity / reviewer Claude.
  c. validation.txt step 14 (and the step 13 row line): the live run it cites, claude1-4-20260806T165326Z-7ac2c9d2, was superseded at 17:16:58Z. Re-observe against the current run and record that one; move the old join to superseded_live_observations.
  d. PR #4386 body "Ownership" section: current pair, and cite the post-rewrite SHAs (e24e3c312, 124372fca, a9736972b, 0c528aad8, 067846932) instead of the three that no longer exist.
  e. Fold in the (3) item below.

(3) NON-BLOCKING, same round. evidence.json AC7 lines 244-245 still overstate the two pins: neither test exercises poll_workers -- both call worker_matches_current_assignment directly and assert False. Say so, e.g. "helper returns False, the precondition the reap/supersede path depends on".

WHAT I AM COMMITTING TO. The implementation is sound and I would approve it on content today. I re-verified on this exact head: RunningWorkerOwnerReconciliationTests 13 passed in 2.69s (/home/lupin/pantheon/.venv/bin/python, PYTHONPATH=.orchestrator); the supervisor.py and test_supervisor.py blobs are byte-identical to the head whose full-suite numbers (619 passed / 162 subtests) and guard wiring I verified line-by-line at 17:37:21Z, so those findings carry forward unchanged. The next head must therefore leave .orchestrator/supervisor.py and .orchestrator/test_supervisor.py byte-identical to 067846932 -- git diff 067846932 <new-head> -- .orchestrator/supervisor.py .orchestrator/test_supervisor.py must be empty. If that holds and (2)+(3) land, I approve. Do not rewrite history this round: the trailer check is green, so add one ordinary evidence commit on top of 067846932 and push without force.

Verification trail: git fetch +refs/heads/task/...; git rev-parse origin/task ref == gh pr view 4386 headRefOid == worktree HEAD == 067846932; git rev-parse of both trees and git diff 073dbfff5 067846932 (empty); git log --format=%s | awk length; check_commit_trailers.py --skip-merge over origin/dev..067846932 (EXIT 0); direct reads of evidence.json, README.md, validation.txt at HEAD; gh pr view 4386 --json body; governed ai-status.sh show; ai-activity-log task_reassigned event at 17:16:28Z and worker_superseded at 17:16:58Z.

## Summary
補上 row owner/reviewer 與 live worker_runner/run records 的 reconcile 機制，避免 helper/fallback 失敗後任務真相漂移。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
