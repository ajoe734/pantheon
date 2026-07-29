# Task Brief: SUP-L12-FLEET-DISPATCH-HEALTH-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Verify live supervisor and provider-first dispatch health
- Status: review (owner refreshed base; pending Codex2 exact-head reapproval)
- Owner: Codex
- Reviewer: Codex2
- Task branch: `task/SUP-L12-FLEET-DISPATCH-HEALTH-20260729`
- Prior approval: PR #4328 head
  `11f303cce07c8ab2be004885766bead27f62dd4d` contains Codex2's
  committed review manifest for owner evidence head
  `bf76772a03f7e97873b9dd161f229e66f4949772`.
- Owner refresh: `origin/dev`
  `6fe626252d10af27eed0aba79530506d192857ca` was merged into the task
  branch. Codex2 must review and approve the refreshed exact head before the
  owner can merge PR #4328 and run governed `done`.

## Summary
Fleet health task to ensure this packet is actually handled by supervisor/auto workers.

## Acceptance
- Prove the live supervisor uses `/home/lupin/pantheon-ci-deploy/dev-root`.
- Prove assistant dev packet `pkt-l12-current-gap-drain-20260729T0107Z`
  reaches a pending, processed, receipt, or archive record.
- Read Antigravity and Claude2 readiness from live worker-runtime facts.
- Record repeated failure loops without editing `.orchestrator/config.json`.

## Evidence
- Review manifest:
  `docs/deployment/evidence/twelve-loop-gap/SUP-L12-FLEET-DISPATCH-HEALTH-20260729/evidence.json`
- Human-readable summary:
  `docs/deployment/evidence/twelve-loop-gap/SUP-L12-FLEET-DISPATCH-HEALTH-20260729/README.md`
- Fleet verdict: acceptance satisfied; live health observed as
  `degraded_but_failover_working`.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
