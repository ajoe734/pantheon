# Task Brief: SUP-L12-FLEET-DISPATCH-HEALTH-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Verify live supervisor and provider-first dispatch health
- Status: review (owner base refresh complete; pending exact-head re-approval)
- Owner: Codex
- Reviewer: Antigravity
- Task branch: `task/SUP-L12-FLEET-DISPATCH-HEALTH-20260729`
- Pull request: `#4328`
- Previous approved head: Antigravity independently approved
  `d171d3e21d741f176f4fee3da4f43bbd6c26a6c0`.
- Base refresh: `dev` advanced to
  `3e0ea2136c1ebe0214e07cfbf6411bb20bb5809a` before the approved head
  could merge. The owner merged that base as
  `a99145a2370331766dfd0161024a3b0ba54ea07c` after the safe integrator
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
- The latest Antigravity approval bound PR #4328 and exact head
  `d171d3e21d741f176f4fee3da4f43bbd6c26a6c0`; it is not reused for the
  refreshed head.

## Owner Closeout Verification
- `python3 -m json.tool evidence.json` passed.
- `sha256sum -c evidence.sha256` passed from the evidence directory.
- `.orchestrator/config.json` remains absent from the task diff.
- The live supervisor still resolves to
  `/home/lupin/pantheon-ci-deploy/dev-root`, and the packet receipt/archive
  remain present.
- The safe auto-integrator dry-run correctly returned `waiting` after `dev`
  advanced, preserving exact-head review semantics.
- The owner merged `origin/dev` at
  `3e0ea2136c1ebe0214e07cfbf6411bb20bb5809a` without changing the reviewed
  fleet evidence or `.orchestrator/config.json`, then reran the focused
  checks before handoff.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
