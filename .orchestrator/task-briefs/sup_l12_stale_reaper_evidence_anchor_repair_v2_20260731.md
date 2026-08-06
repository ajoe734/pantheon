# Task Brief: SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-V2-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Supersede Wave0X #4385 evidence-anchor repair with current-head spec
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: REOPEN round 2 - head unchanged, fix never pushed. PR #4465 head is still c44f0518fa49b94dc40bc153c147d5a86a280fdf (gh updatedAt 2026-08-06T13:39:48Z, i.e. BEFORE the 13:46:06Z reopen) and is identical to my prior tag pantheon-review/reopen/c44f0518. Defects A-C are unfixed on the reviewable head: (A) repair README.md still asserts 'pass again after composing the latest origin/dev head' instead of scoping to composed head 941c15a3; (B) the task brief still ends with a trailing blank line; (C) evidence.json verification.command_runtime_sha is still 728fa361b (a git commit) not the live runtime SHA f90e0aae6cb5e86f18b20db9f30bc834f6115745. The fix commit a5ff4b5f0fc295aed7c927fd0ec22bff773d737e exists only in the task worktree - push it. (D) NEW defect inside that unpushed commit: evidence.json delivery.task_head_sha=e5eef026f32cc1faa5b35fd4f5399e723465df26 is an orphaned amend sibling (its parent is c44f0518 and 'git merge-base --is-ancestor e5eef026 a5ff4b5f0' returns false), so it will not exist on origin after push; it must name a real ancestor of the pushed head, e.g. c44f0518. Verified sound at this head and NOT to be redone: the core anchor repair (subject README plus both evidence.json anchor fields now 9d53a94a295d71ee49aea6f4b96e47fbcfd29093, which is an ancestor of the PR head; invalid 9d53a94a265c55af4c8d15c50ab3751f1440ac0f absent from every asserted-anchor position), .orchestrator/config.json untouched in origin/dev...c44f0518, and the out-of-scope dev-side ImportError (provider_auth_probe_due missing from provider_permissions.py) independently reproduced at this head.

## Summary
Supersedes preempted immutable task SUP-L12-STALE-REAPER-EVIDENCE-ANCHOR-REPAIR-20260731 after bridge rejected spec update. Repair #4385 nonexistent evidence anchor before stale-reaper can satisfy Wave 0.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
