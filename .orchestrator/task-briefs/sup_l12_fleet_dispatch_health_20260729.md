# Task Brief: SUP-L12-FLEET-DISPATCH-HEALTH-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Verify live supervisor and provider-first dispatch health
- Status: review (owner base refresh complete; pending exact-head re-approval)
- Owner: Codex2
- Reviewer: Antigravity
- Task branch: `task/SUP-L12-FLEET-DISPATCH-HEALTH-20260729`
- Pull request: `#4328`
- Approved head: Antigravity independently approved
  `5ebecbd4fbb762566c4ca51b7f999d75e02454b4`.
- Base refresh: `dev` advanced to
  `f9063be7da0106c43039042ea6edfdbd33a0bb51` before the approved head
  could merge. The owner merged that base as
  `4ca2ab122871aeb12c9cd9f467a5055c6b748f2f` after the safe integrator
  rejected the stale head.
- Next: Antigravity independently reviews the refreshed exact head and
  re-approves PR #4328. Human/Ops then supplies the required root-freeze
  context on that same head. The owner reruns the exact-head integrator and
  governed `done`; ownership and reviewer assignment remain unchanged.

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
- The Antigravity approval bound PR #4328 and exact head
  `5ebecbd4fbb762566c4ca51b7f999d75e02454b4`; it is not reused for a
  refreshed head.

## Owner Closeout Verification
- `python3 -m json.tool evidence.json` passed.
- `sha256sum -c evidence.sha256` passed from the evidence directory.
- `.orchestrator/config.json` remains absent from the task diff.
- Live supervisor command root and packet receipt/archive remained proven at
  Antigravity's exact-head review.
- The canonical review gate succeeded for `5ebecbd4fbb762566c4ca51b7f999d75e02454b4`.
- The safe auto-integrator dry-run correctly returned `waiting` after `dev`
  advanced, preserving exact-head review semantics.
- The owner merged `origin/dev` at
  `f9063be7da0106c43039042ea6edfdbd33a0bb51` without changing the reviewed
  fleet evidence or `.orchestrator/config.json`, then reran the focused checks
  before handoff.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
