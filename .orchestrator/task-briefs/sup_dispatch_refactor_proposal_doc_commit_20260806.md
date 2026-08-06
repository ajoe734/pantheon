# Task Brief: SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT-20260806

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Commit the missing supervisor dispatch refactor proposal doc
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Rebind commit trailers to Owner: Antigravity, Reviewer: Claude and handoff to Claude for review.

VERIFIED GOOD - do not redo this work:
- PR #4587 head c3261d07c, base dev, MERGEABLE. Two-dot diff vs origin/dev is exactly 2 added files, 290 insertions, 0 deletions, 0 modifications: docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md (222) + the task brief (68). No code/behavior change - acceptance criterion 3's 'no code change' half holds.
- Committed blob is byte-identical to the status-root copy (/home/lupin/pantheon/docs/04/...md, mtime Aug 4 14:14): diff of 'git show c3261d07c:docs/04/...' against it is empty. Owner's byte-identity claim confirmed independently.
- Acceptance criterion 3 (content matches what the five 2026-08-04 tasks cite) CONFIRMED against the real rows, not the owner's table: four archived rows in ai-task-archive/tasks/ (PROVIDER-PROBE-HYSTERESIS, DISPATCH-EXPLAIN-TOOL, STATUS-OUTBOX-INTEGRITY-VISIBILITY, TASK-FAILURE-STREAK-SCHEMA, all done) + the live blocked SUP-BLOCKED-TASK-RECONCILIATION-20260804. Each cites 'Problem N' by name and each section resolves in the committed file (headings at lines 26/48/63/83/156), with matching substance: Problem 1 carries the load_1m ~3-4 flapping repro and the hysteresis/transition-event fix shape; Problem 2 names the same five gate functions plus the block-reason->quota->capacity->catalog-lock->dependency->cooldown order; Problem 3 carries the 'activity content-addressed archives do not match lineage' repro and STATUS_*_OUTBOX_KEY/status_write_pending shape; Problem 4 carries the 4-retry worker_failed repro on SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803 plus failure_streak/quarantined; Problem 5 carries the owned/review/finalize status lists, the five-task reaper graveyard (all five names match), check_kind github_pr_ci/task_dependency, reconcile_blocked_tasks on its own interval, and the explicit retire-the-family success criterion.
- Criterion 1 (exists on dev / readable from a fresh dev-based worktree) is satisfiable by merging this PR as-is; the file is genuinely absent from origin/dev today.
- Problem 5 appearing after 'Suggested sequencing', and the undeclared Problem 6 section, are both fine - leaving them preserves the byte-identity that makes the restore auditable.
- scripts/git/check_commit_trailers.py --range origin/dev..c3261d07c exits 0. CI checks (Commit trailers / Runtime mirror guard / Python packaging provision) are QUEUED, not failing.

BLOCKER - fix before this can be approved:
Ownership was swapped by the supervisor at 2026-08-06T16:46:20Z (task_reassigned: owner Claude->Antigravity AND reviewer Antigravity->Claude, one combined event). Head c3261d07c was authored 16:37:22Z, nine minutes BEFORE that swap, so it still carries 'LLM-Agent: Claude' and 'Reviewer: Antigravity', and the committed brief header still reads 'Owner: Claude / Reviewer: Antigravity'. That makes closeout impossible at this head, verified by reading scripts/ai_status.py in the command root:
  (a) collect_done_delivery_metadata (~line 2995) compares the HEAD 'Reviewer' trailer against canonical_agent_name(task['reviewer']). 'Antigravity' != 'Claude' -> mismatched_fields -> hard SystemExit. Reviewer has no reassignment escape hatch; only LLM-Agent does.
  (b) the LLM-Agent escape hatch does not save it either: _verified_done_owner_reassignment (~line 6245) requires the audited reassignment to have old_reviewer == new_reviewer == current reviewer. The 16:46:20Z event moved the reviewer too, so it exits with 'the latest audited owner reassignment does not bind the commit owner to the current owner with reviewer continuity.'
So if I approved this head, the PR would merge and then Antigravity's 'done' would fail closed with no in-PR way to repair it.

REQUIRED FIX (one commit, no rework of the doc):
Add ONE in-scope commit on task/SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT-20260806 that (1) updates .orchestrator/task-briefs/sup_dispatch_refactor_proposal_doc_commit_20260806.md to the current pair - Owner: Antigravity, Reviewer: Claude - keeping the existing acceptance-verification content rather than replacing it with the regenerated stub, and (2) carries trailers 'LLM-Agent: Antigravity' and 'Reviewer: Claude' with 'Task-ID: SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT-20260806', subject under 72 chars and containing the task id. Do NOT touch docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md - its byte-identity to the status-root original is a reviewed property and re-verified above. Then handoff for a second review pass; I will re-verify only the new commit.

## Summary
-

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
