# Task Brief: SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Promote merged alias reassignment guard into live supervisor runtime
- Status: in_progress
- Owner: Antigravity
- Reviewer: Human/Ops
- Next: Human/Ops rejects PR #4431 exact head 7990dea76dd18471d3fdc8b00d14826806cc5ee0 as evidence/CI-only correction; do not restart or otherwise mutate the now-stable supervisor PID 3523046. GitHub Commit trailers failed on both push and pull_request because commit 7990dea subject length is 99 characters (>72); Smoke acceptance was skipped. Amend the head non-interactively to a compliant subject/trailers and push with force-with-lease only after verifying the exact remote head. The corrected evidence must preserve, not erase, the full incident chronology: first disposable-worktree promotion PID 3497098, worktree HEAD drift to ac4fc7fe0e03cbd125389a830efde2873aedb73e, deleted cwd after worker cleanup, false active_worker_leases_preserved=0 claim, Human/Ops admission-lock rollback at 23:26:14Z to cbb36ff1fe385f3bc2690124ff22d8edc0056896/PID 3509070, and unchanged config SHA. Add raw or exact structured samples for persistent root HEAD/tree/clean/origin, PID/cwd, at least three completed UTC loops, active worker heartbeat/queue lease preservation, projection caught_up plus equal projected/expected hashes, duplicate task count method/result, provider primary readiness, rollback root availability, and config hash before/after. Do not claim automatic rollback was exercised: explicitly record that the first failure required Human/Ops live rescue, and add a fail-closed rollback-readiness validation/command contract for future postcheck failure or mark that original acceptance item unmet. Evidence status remains review until independent approval. Push a new exact head, wait for every required check, and hand off again. No config writer, runtime swap, product/DAG work, or canonical JSON edit.

## Summary
Supervisor-dispatched Antigravity runtime promotion only. Expected branch task/SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731, clean governed worktree, merge target dev. Owner capability: supervisor runtime deployment and readback. Reviewer capability: independent Human/Ops exact-head/runtime evidence validation.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
