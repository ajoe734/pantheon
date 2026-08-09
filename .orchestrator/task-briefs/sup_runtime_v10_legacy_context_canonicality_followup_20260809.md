# Task Brief: SUP-RUNTIME-V10-LEGACY-CONTEXT-CANONICALITY-FOLLOWUP-20260809

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Bind historical generated task-brief canonicality for legacy bootstrap
- Status: in_progress
- Owner: Antigravity2
- Reviewer: Codex2
- Next: Reject PR #4671 head 8df5321eee9806009c2e4ce993a5e01a885f6381. Adding live path/length/SHA tuples is necessary but not sufficient: runtime code still does not bind or verify authoritative_event_id, legacy_command_runtime_sha, or prevention_boundary_sha. Current missing-event test mocks generic task-known=false; current prevention-boundary test only mutates bytes; there are no wrong-event, wrong-source-SHA, or wrong-boundary-SHA negatives. Implement structured exact binding fields. Bind sup_dispatch event supervisor-task-failure-streak-a9d6b8a54889ffae650c47e67d004eff0dd93f691a92791345e2bcd38cbdccf6 and fleet event ai-status-event-739b819ac053e3cdd0a58d6b12311705d553cc44cb372f3298302b2a5b337aea; legacy source 5877b64425c8d6aede147d6cbbc6fbb9e228c259; prevention boundary f5570754e6b9534893fc65744e82abe7f0ff0a74. Verify source and boundary are ancestors of expected_head and exact reviewed event metadata matches; add second live-byte positive plus wrong-event/source/boundary negatives; correct evidence claims; then fresh exact-head review.

## Summary
PR #4667 retained strict byte equality by re-rendering legacy brief content from current bound config, but the active pre-fix brief now fails that comparison before any promotion mutation. Repair only a fail-closed historical-context provenance boundary for the known generated residue; do not broaden drift acceptance or touch the live runtime.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
